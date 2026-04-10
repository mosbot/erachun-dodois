"""
Dodois upload logic: validate invoice readiness, build payload, upload.
"""
import json
import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from app.core.ubl_parser import UBLInvoice, UBLLineItem
from app.db.models import Invoice, SupplierMapping, get_product_mapping

TAX_RATE_IDS = {
    25: "c2f84413b0f6bba911ee2c6a71f95c44",
    13: "e67b8c27d336ae8811ede297eb178d65",
    5:  "7ec6687213e7bc4e11ee5e0c2fe41370",
    0:  "11eef744519b7360879c21c0dc22e49c",
}

_CENT = Decimal("0.01")


def _round2(value) -> float:
    """Round to 2 decimals using HALF_UP (matches the Dodois .NET server).

    Python's float ``round()`` uses banker's rounding on imprecise floats
    (e.g. ``round(1.075, 2) == 1.07`` because 1.075 is stored as 1.0749999…),
    which diverges from Dodois at half-cent boundaries and causes
    ``SUPPLY_ITEM_PRICE_PER_UNIT_WITH_VAT_WRONG_CALCULATION`` rejections.
    """
    return float(Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP))


def validate_invoice(session, invoice: Invoice, ubl: UBLInvoice) -> list:
    """Return list of human-readable blocking issues. Empty list = ready to upload."""
    issues = []

    mapping = session.query(SupplierMapping).filter_by(
        eracun_oib=invoice.sender_oib, enabled=True
    ).first()
    if not mapping or not mapping.dodois_catalog_id:
        issues.append("Поставщик не настроен для Dodois")
        return issues

    if not invoice.dodois_pizzeria:
        issues.append("Пиццерия не выбрана")

    unmapped = []
    for line in ubl.lines:
        desc = (line.item_name or line.description or "").strip()
        if not desc:
            continue
        pm = get_product_mapping(
            session, mapping.id, desc,
            line.standard_item_id or None,
        )
        if not pm or not pm.dodois_raw_material_id:
            unmapped.append(desc)

    if unmapped:
        names = ", ".join(unmapped[:3])
        suffix = f" и ещё {len(unmapped) - 3}" if len(unmapped) > 3 else ""
        issues.append(f"{len(unmapped)} товаров без маппинга: {names}{suffix}")

    return issues


def _aggregate_lines(lines: list) -> list:
    """Merge duplicate lines (same item_name) by summing quantities and amounts."""
    grouped = defaultdict(list)
    for line in lines:
        key = (line.item_name or line.description or "").strip()
        if key:
            grouped[key].append(line)

    result = []
    for key, group in grouped.items():
        if len(group) == 1:
            result.append(group[0])
            continue
        agg = UBLLineItem(
            item_name=group[0].item_name,
            description=group[0].description,
            quantity=sum(l.quantity for l in group),
            unit_code=group[0].unit_code,
            line_total=sum(l.line_total for l in group),
            tax_percent=group[0].tax_percent,
            tax_amount=sum(l.tax_amount for l in group),
            standard_item_id=group[0].standard_item_id,
        )
        agg.unit_price = round(agg.line_total / agg.quantity, 4) if agg.quantity else 0
        result.append(agg)
    return result


def _compute_supply_quantity(line, mat) -> float:
    """Transform the XML line quantity into what Dodois expects in the payload.

    For weighed materials (unit=5 grams, no container) Dodois stores quantity
    in grams, so a KGM line must be scaled ×1000. Everything else (containers,
    piece-count materials) passes through as-is.
    """
    if mat.dodois_container_id is None and mat.unit == 5:
        if (line.unit_code or "").upper() == "KGM":
            return line.quantity * 1000
    return line.quantity


def _compute_price_per_unit(total_price: float, qty_payload: float, mat) -> float:
    """Calculate pricePerUnit using Dodois container formula.

    ``qty_payload`` is the quantity value that will be sent in the payload,
    i.e. already transformed by ``_compute_supply_quantity``. Arithmetic is
    done with :class:`~decimal.Decimal` + ``ROUND_HALF_UP`` so the result
    matches the Dodois server at half-cent boundaries.
    """
    total = Decimal(str(total_price))
    qty = Decimal(str(qty_payload))
    cs = Decimal(str(mat.container_size))

    if mat.dodois_container_id is None:
        if mat.unit == 5:  # weighed (grams) → price per kg
            divisor = qty / Decimal("1000")
        else:  # piece count (Vindi Sok, unit=1)
            divisor = qty
    elif mat.unit == 5:  # grams → price per kg
        divisor = qty * cs / Decimal("1000")
    else:  # unit=1 (pcs) or unit=8 (meters)
        divisor = qty * cs
    return float((total / divisor).quantize(_CENT, rounding=ROUND_HALF_UP))


def build_supply_payload(
    session,
    invoice: Invoice,
    ubl: UBLInvoice,
    pizzeria_cfg: dict,
    skip_unmapped: bool = False,
) -> tuple[dict, list[str]]:
    """Build POST /Accounting/v1/incomingstock/supplies payload.

    Returns (payload, skipped_descriptions). When skip_unmapped is False,
    raises ValueError on the first unmapped line. When True, those lines
    are silently dropped and their descriptions are returned in the second
    tuple element.
    """
    mapping = session.query(SupplierMapping).filter_by(
        eracun_oib=invoice.sender_oib
    ).first()
    supplier_id = mapping.dodois_supplier.dodois_id

    inv_date = invoice.issue_date.strftime("%Y-%m-%d") if invoice.issue_date else datetime.utcnow().strftime("%Y-%m-%d")
    receipt_dt = f"{inv_date}T12:00:00"

    supply_items: list[dict] = []
    skipped: list[str] = []
    for line in _aggregate_lines(ubl.lines):
        desc = (line.item_name or line.description or "").strip()
        pm = get_product_mapping(session, mapping.id, desc, line.standard_item_id or None)
        if not pm or not pm.raw_material:
            if skip_unmapped:
                skipped.append(desc)
                continue
            raise ValueError(f"No mapping for line: {desc}")
        mat = pm.raw_material

        total_without_vat = _round2(line.line_total)
        # Some Croatian suppliers (e.g. Pivac) omit per-line TaxAmount and only
        # report it at document level. Fall back to computing it from the
        # classified tax rate when the parser didn't pick one up.
        raw_tax_amount = line.tax_amount
        if raw_tax_amount <= 0 and line.tax_percent > 0:
            raw_tax_amount = line.line_total * line.tax_percent / 100
        vat_value = _round2(raw_tax_amount)
        total_with_vat = _round2(Decimal(str(total_without_vat)) + Decimal(str(vat_value)))
        tax_id = TAX_RATE_IDS.get(int(line.tax_percent), TAX_RATE_IDS[25])

        qty_payload = _compute_supply_quantity(line, mat)
        # Compute BOTH pricePerUnit values directly from their respective
        # totals.  The Dodois server validates each independently:
        #   ppuWithoutVat ≈ round(totalWithoutVat / divisor)
        #   ppuWithVat    ≈ round(totalWithVat    / divisor)
        # Deriving ppuWithVat from ppuWithoutVat × (1 + taxRate) introduces
        # double-rounding that drifts by ±€0.01 on ~5 % of lines (seen on
        # Stanić 17423/V850/900: Fetakos, Sol, cafe latte, Ananas, Pileća).
        ppu_without_vat = _compute_price_per_unit(total_without_vat, qty_payload, mat)
        ppu_with_vat = _compute_price_per_unit(total_with_vat, qty_payload, mat)
        supply_items.append({
            "quantity": qty_payload,
            "rawMaterialId": mat.dodois_material_id,
            "rawMaterialContainerId": mat.dodois_container_id,
            "taxId": tax_id,
            "vatValue": vat_value,
            "totalPriceWithVat": total_with_vat,
            "totalPriceWithoutVat": total_without_vat,
            "pricePerUnitWithVat": ppu_with_vat,
            "pricePerUnitWithoutVat": ppu_without_vat,
        })

    if not supply_items:
        raise ValueError("No mapped lines to upload")

    payload = {
        "id": uuid.uuid4().hex,
        "supplierId": supplier_id,
        "unitId": pizzeria_cfg["unit_id"],
        "invoiceNumber": invoice.invoice_number,
        "commercialInvoiceNumber": invoice.invoice_number,
        "invoiceDate": inv_date,
        "commercialInvoiceDate": inv_date,
        "receiptDateTime": receipt_dt,
        "hasVat": True,
        "currencyCode": 978,
        "supplyItems": supply_items,
    }
    return payload, skipped


def upload_invoice(
    session,
    invoice: Invoice,
    ubl: UBLInvoice,
    client,
    pizzeria_cfg: dict,
    skip_unmapped: bool = False,
) -> tuple[str, list[str]]:
    """Upload invoice to Dodois. Returns (supply_id, skipped_descriptions).
    Updates invoice in DB. Raises on API failure — caller sets error status.
    """
    payload, skipped = build_supply_payload(
        session, invoice, ubl, pizzeria_cfg, skip_unmapped=skip_unmapped
    )
    result = client.create_supply(payload)
    supply_id = result.get("id", payload["id"])
    invoice.dodois_supply_id = supply_id
    invoice.dodois_upload_partial = bool(skipped)
    invoice.dodois_skipped_count = len(skipped)
    invoice.dodois_skipped_lines = json.dumps(skipped, ensure_ascii=False) if skipped else None
    invoice.processing_status = "uploaded_to_dodois"
    session.commit()
    return supply_id, skipped
