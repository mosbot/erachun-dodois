"""
Dodois upload logic: validate invoice readiness, build payload, upload.
"""
import uuid
from collections import defaultdict
from datetime import datetime

from app.core.ubl_parser import UBLInvoice, UBLLineItem
from app.db.models import Invoice, SupplierMapping, get_product_mapping

TAX_RATE_IDS = {
    25: "c2f84413b0f6bba911ee2c6a71f95c44",
    13: "e67b8c27d336ae8811ede297eb178d65",
    5:  "7ec6687213e7bc4e11ee5e0c2fe41370",
    0:  "11eef744519b7360879c21c0dc22e49c",
}


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


def _compute_price_per_unit(total_price: float, qty: float, mat) -> float:
    """Calculate pricePerUnit using Dodois container formula."""
    if mat.dodois_container_id is None:
        divisor = qty
    elif mat.unit == 5:  # grams → price per kg
        divisor = qty * mat.container_size / 1000
    else:  # unit=1 (pcs) or unit=8 (meters)
        divisor = qty * mat.container_size
    return round(total_price / divisor, 2)


def build_supply_payload(session, invoice: Invoice, ubl: UBLInvoice, pizzeria_cfg: dict) -> dict:
    """Build POST /Accounting/v1/incomingstock/supplies payload."""
    mapping = session.query(SupplierMapping).filter_by(
        eracun_oib=invoice.sender_oib
    ).first()
    supplier_id = mapping.dodois_supplier.dodois_id

    inv_date = invoice.issue_date.strftime("%Y-%m-%d") if invoice.issue_date else datetime.utcnow().strftime("%Y-%m-%d")
    receipt_dt = f"{inv_date}T12:00:00"

    supply_items = []
    for line in _aggregate_lines(ubl.lines):
        desc = (line.item_name or line.description or "").strip()
        pm = get_product_mapping(session, mapping.id, desc, line.standard_item_id or None)
        mat = pm.raw_material

        total_without_vat = round(line.line_total, 2)
        total_with_vat = round(line.line_total + line.tax_amount, 2)
        vat_value = round(line.tax_amount, 2)
        tax_id = TAX_RATE_IDS.get(int(line.tax_percent), TAX_RATE_IDS[25])

        supply_items.append({
            "quantity": line.quantity,
            "rawMaterialId": mat.dodois_material_id,
            "rawMaterialContainerId": mat.dodois_container_id,
            "taxId": tax_id,
            "vatValue": vat_value,
            "totalPriceWithVat": total_with_vat,
            "totalPriceWithoutVat": total_without_vat,
            "pricePerUnitWithVat": _compute_price_per_unit(total_with_vat, line.quantity, mat),
            "pricePerUnitWithoutVat": _compute_price_per_unit(total_without_vat, line.quantity, mat),
        })

    return {
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


def upload_invoice(session, invoice: Invoice, ubl: UBLInvoice, client, pizzeria_cfg: dict) -> str:
    """Upload invoice to Dodois. Returns supply ID. Updates invoice in DB.
    Raises exception on API failure — caller is responsible for setting error status.
    """
    payload = build_supply_payload(session, invoice, ubl, pizzeria_cfg)
    result = client.create_supply(payload)
    supply_id = result.get("id", payload["id"])
    invoice.dodois_supply_id = supply_id
    invoice.processing_status = "uploaded_to_dodois"
    session.commit()
    return supply_id
