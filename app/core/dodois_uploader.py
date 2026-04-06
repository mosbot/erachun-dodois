"""
Dodois upload logic: validate invoice readiness, build payload, upload.
"""
from app.core.ubl_parser import UBLInvoice
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
