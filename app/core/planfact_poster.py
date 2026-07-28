"""
Turn stored Wolt/Glovo invoices into PlanFact outcome operations.

Only these two suppliers are posted. The money comes straight from the stored
totals — the previous service reconstructed the commission by subtraction,
which is one rounding step we do not need.
"""
import logging
import re
from typing import Optional

from app.db.models import Invoice

logger = logging.getLogger(__name__)

# "Glovo provizija P705447 račun broj: 47284-1-5-2026"
_GLOVO_INNER_NUMBER = re.compile(r"ra[čc]un\s+broj[:\s]+([\d\-]+)", re.IGNORECASE)

_CENT = 0.01


def _planfact_cfg(cfg: dict) -> dict:
    return (cfg or {}).get("planfact", {}) or {}


def resolve_provider(cfg: dict, invoice: Invoice) -> Optional[str]:
    """Identify the provider by supplier OIB, not by display name."""
    providers = _planfact_cfg(cfg).get("providers", {}) or {}
    oib = (invoice.sender_oib or "").strip()
    for name, conf in providers.items():
        if oib and oib == str(conf.get("oib", "")).strip():
            return name
    return None


def extract_external_id(provider: str, invoice: Invoice,
                        xml_text: str) -> Optional[str]:
    """Return the key PlanFact knows this invoice by.

    Wolt uses the same number in both systems. Glovo does not: eRačun calls it
    ``2653343/G1/2234278`` while PlanFact holds ``47262-1-5-2026``, which the
    XML carries inside the line item name.
    """
    if provider == "wolt":
        return (invoice.invoice_number or "").strip() or None
    if provider == "glovo":
        m = _GLOVO_INNER_NUMBER.search(xml_text or "")
        return m.group(1) if m else None
    return None


def validate_invoice(cfg: dict, invoice: Invoice, provider: Optional[str],
                     external_id: Optional[str]) -> list:
    """Return blocking issues. Empty list means the invoice may be posted."""
    pf = _planfact_cfg(cfg)
    issues = []

    if not provider:
        issues.append("Unknown provider — supplier OIB is not Wolt or Glovo")
        return issues

    series = (invoice.invoice_number or "").split("-")
    excluded = (pf.get("providers", {}).get(provider, {}) or {}).get(
        "exclude_series", []) or []
    if len(series) >= 3 and series[1] in excluded:
        issues.append("Wolt Drive invoice — not booked to PlanFact")

    if invoice.document_type_id == 381:
        issues.append("Credit note — PlanFact posting is not supported")

    if not invoice.dodois_pizzeria:
        issues.append("Pizzeria could not be determined")
    elif invoice.dodois_pizzeria not in (pf.get("projects", {}) or {}):
        issues.append(
            f"No PlanFact project mapped for pizzeria {invoice.dodois_pizzeria}")

    if not invoice.vat_date:
        issues.append("VAT date is missing")

    if not external_id:
        issues.append("External id could not be determined")

    net = invoice.total_without_vat or 0.0
    vat = invoice.total_vat or 0.0
    gross = invoice.total_with_vat or 0.0
    if gross <= 0:
        issues.append("Total amount is zero or negative")
    elif abs(net + vat - gross) > _CENT:
        issues.append(
            f"Amounts do not add up: {net} + {vat} != {gross}")

    return issues


def select_candidates(session, cfg: dict, limit: Optional[int] = None) -> list:
    """Invoices from Wolt/Glovo that carry no PlanFact operation yet."""
    pf = _planfact_cfg(cfg)
    oibs = [str((conf or {}).get("oib", "")).strip()
            for conf in (pf.get("providers", {}) or {}).values()]
    oibs = [o for o in oibs if o]
    if not oibs:
        return []

    q = (session.query(Invoice)
         .filter(Invoice.processing_status != "deleted")
         .filter(Invoice.planfact_operation_id.is_(None))
         .filter(Invoice.sender_oib.in_(oibs))
         .order_by(Invoice.vat_date.asc(), Invoice.id.asc()))
    if limit:
        q = q.limit(limit)
    return q.all()
