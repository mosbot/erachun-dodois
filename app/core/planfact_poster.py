"""
Turn stored Wolt/Glovo invoices into PlanFact outcome operations.

Only these two suppliers are posted. The money comes straight from the stored
totals — the previous service reconstructed the commission by subtraction,
which is one rounding step we do not need.
"""
import logging
import re
from datetime import timedelta
from typing import Optional

from lxml import etree

from app.core.planfact_client import PlanfactError
from app.db.models import Invoice

logger = logging.getLogger(__name__)

# "Glovo provizija P705447 račun broj: 47284-1-5-2026" — require the shape
# PlanFact's number actually has (four numeric groups joined by dashes),
# not just any digits and dashes. The looser [\d\-]+ also captured payment
# boilerplate like "Placanje na racun broj: 2360000-1101234567" or
# "racun broj: -" wherever it appeared in the document. See finding I3.
_GLOVO_INNER_NUMBER = re.compile(
    r"RA[ČC]UN\s+BROJ[:\s]+(\d+-\d+-\d+-\d+)", re.IGNORECASE)

_LINE_ITEM_NS = {
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}

_CENT = 0.01


def _line_item_names(xml_text: str) -> list:
    """Text of every line-item name in the document.

    cbc:Note and cac:PaymentMeans precede cac:InvoiceLine in UBL document
    order and routinely carry payment boilerplate ("uplata na racun broj:
    …"), which is exactly what won the old whole-document regex search.
    Restricting the search to line items (cac:InvoiceLine, and
    cac:CreditNoteLine for the credit-note document shape) is the other
    half of the finding I3 fix — the tightened regex alone is not enough,
    since boilerplate could coincidentally have four dash-separated groups.

    Returns [] on any parse failure rather than raising — a document that
    cannot be parsed simply yields no match, which is the fail-safe
    behaviour extract_external_id already relies on.
    """
    if not xml_text:
        return []
    raw = xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text
    try:
        root = etree.fromstring(raw)
    except etree.XMLSyntaxError:
        return []
    names = []
    for xpath in (".//cac:InvoiceLine/cac:Item/cbc:Name",
                  ".//cac:CreditNoteLine/cac:Item/cbc:Name"):
        for el in root.findall(xpath, _LINE_ITEM_NS):
            if el.text:
                names.append(el.text)
    return names


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
    XML carries inside a line item name — and only there; searching the
    whole document risks matching unrelated payment boilerplate (finding I3).
    """
    if provider == "wolt":
        return (invoice.invoice_number or "").strip() or None
    if provider == "glovo":
        for name in _line_item_names(xml_text):
            m = _GLOVO_INNER_NUMBER.search(name)
            if m:
                return m.group(1)
        return None
    return None


def validate_invoice(cfg: dict, invoice: Invoice, provider: Optional[str],
                     external_id: Optional[str]) -> list:
    """Return blocking issues. Empty list means the invoice may be posted."""
    pf = _planfact_cfg(cfg)
    issues = []

    if not provider:
        issues.append("Unknown provider — supplier OIB is not Wolt or Glovo")
        return issues

    # Check that provider is configured in accounts and categories
    accounts = pf.get("accounts", {}) or {}
    categories = pf.get("categories", {}) or {}
    if provider not in accounts:
        issues.append(f"Provider {provider} is not configured in PlanFact accounts")
    if f"{provider}_commission" not in categories:
        issues.append(f"Commission category for {provider} is not configured")
    if "vat" not in categories:
        issues.append("VAT category is not configured")

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


COMMENT_MARKER = "#erachun"
# Prefix on errors that mean "this invoice is deliberately not posted" rather
# than "posting was attempted and went wrong". Callers use it to stay quiet.
BLOCKED_PREFIX = "Blocked: "
# How far either side of the VAT date to look for an existing operation.
LOOKUP_WINDOW_DAYS = 3


def _iso(dt) -> str:
    return dt.strftime("%Y-%m-%dT00:00:00Z")


def build_payload(cfg: dict, invoice: Invoice, provider: str,
                  external_id: str) -> dict:
    pf = _planfact_cfg(cfg)
    project_id = pf["projects"][invoice.dodois_pizzeria]
    commission_category = pf["categories"][f"{provider}_commission"]
    vat_category = pf["categories"]["vat"]
    when = _iso(invoice.vat_date)

    return {
        "operationDate": when,
        "isCommitted": True,
        "accountId": pf["accounts"][provider],
        "value": round(invoice.total_with_vat, 2),
        "comment": f"{COMMENT_MARKER} {provider.capitalize()} {external_id}",
        "externalId": external_id,
        "items": [
            {
                "operationCategoryId": commission_category,
                "projectId": project_id,
                "value": round(invoice.total_without_vat, 2),
                "calculationDate": when,
                "isCalculationCommitted": True,
            },
            {
                "operationCategoryId": vat_category,
                "projectId": project_id,
                "value": round(invoice.total_vat, 2),
                "calculationDate": when,
                "isCalculationCommitted": True,
            },
        ],
    }


def find_existing_operation(client, cfg: dict, invoice: Invoice, provider: str,
                            external_id: str):
    """Return the id of an operation already carrying this external id.

    PlanFact does not enforce externalId uniqueness, so this is the only thing
    standing between a retry and a duplicate.
    """
    account_id = _planfact_cfg(cfg)["accounts"][provider]
    start = (invoice.vat_date - timedelta(days=LOOKUP_WINDOW_DAYS)).strftime("%Y-%m-%d")
    end = (invoice.vat_date + timedelta(days=LOOKUP_WINDOW_DAYS)).strftime("%Y-%m-%d")
    for op in client.list_operations(account_id, start, end):
        if str(op.get("externalId") or "") == external_id:
            return str(op.get("operationId"))
    return None


def post_invoice(client, cfg: dict, invoice: Invoice, xml_text: str,
                 dry_run: bool = False):
    """Post one invoice. Returns (operation_id, error) — exactly one is set.

    (None, None) means the invoice was validated but nothing was sent because
    this is a dry run.

    Whenever an operation id is returned (found already posted, freshly
    created, or found via the empty-body fallback lookup), ``invoice`` is
    mutated in place to set ``planfact_external_id`` alongside it — this is
    the match key the spec calls "computed once, stored" (finding M1); the
    caller still owns the session/commit.
    """
    provider = resolve_provider(cfg, invoice)
    external_id = extract_external_id(provider, invoice, xml_text) if provider else None

    issues = validate_invoice(cfg, invoice, provider, external_id)
    if issues:
        # Marked so callers can tell a deliberate skip from a real failure.
        # Wolt Drive arrives twice a month and is never booked by design;
        # alerting on it trains the reader to ignore alerts.
        return None, BLOCKED_PREFIX + "; ".join(issues)

    try:
        existing = find_existing_operation(client, cfg, invoice, provider, external_id)
        if existing:
            logger.info("Invoice %s already in PlanFact as operation %s",
                        invoice.invoice_number, existing)
            invoice.planfact_external_id = external_id
            return existing, None

        if dry_run:
            return None, None

        payload = build_payload(cfg, invoice, provider, external_id)
        response = client.create_outcome(payload)
        op_id = ((response or {}).get("data") or {}).get("operationId")
        if op_id:
            invoice.planfact_external_id = external_id
            return str(op_id), None

        # Empty or unexpected body: the operation may still have been created.
        # Look it up rather than risk posting it twice.
        found = find_existing_operation(client, cfg, invoice, provider, external_id)
        if found:
            invoice.planfact_external_id = external_id
            return found, None
        return None, "PlanFact returned no operation id and none was found afterwards"

    except PlanfactError as exc:
        return None, str(exc)
    except Exception as exc:
        logger.exception("Unexpected error posting invoice %s", invoice.invoice_number)
        return None, f"Unexpected error: {exc}"


def should_notify(previous_error: Optional[str], new_error: str) -> bool:
    """Notify only when a failure is new or has changed.

    The poster runs every 30 minutes and a failing invoice stays in the queue,
    so notifying every time would send the same message 48 times a day.
    """
    return (previous_error or "") != (new_error or "")
