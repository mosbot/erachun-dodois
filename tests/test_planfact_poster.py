from datetime import datetime

from app.core.planfact_poster import (
    resolve_provider, extract_external_id, validate_invoice, select_candidates,
)
from app.db.models import Invoice

CFG = {
    "planfact": {
        "enabled": True,
        "accounts": {"wolt": 666927, "glovo": 666928},
        "projects": {"Zagreb-1": 1172400, "Zagreb-2": 1198217},
        "categories": {"wolt_commission": 8563181,
                       "glovo_commission": 8563431, "vat": 9485374},
        "providers": {
            "wolt": {"oib": "25531986377",
                     "exclude_series": ["2553198637741"]},
            "glovo": {"oib": "48879371584", "exclude_series": []},
        },
    }
}

GLOVO_XML = ('<Invoice><Item><Name>Glovo provizija P705447 '
             'račun broj: 47262-1-5-2026</Name></Item></Invoice>')


def _inv(**kw):
    base = dict(
        document_nr="90247-2553198637711-2026",
        invoice_number="90247-2553198637711-2026",
        sender_oib="25531986377", sender_name="Wolt Zagreb d.o.o.",
        document_type_id=1, dodois_pizzeria="Zagreb-1",
        issue_date=datetime(2026, 7, 10), vat_date=datetime(2026, 7, 10),
        total_without_vat=636.91, total_vat=159.23, total_with_vat=796.14,
        processing_status="parsed",
    )
    base.update(kw)
    return Invoice(**base)


def test_provider_resolved_by_oib():
    assert resolve_provider(CFG, _inv()) == "wolt"
    assert resolve_provider(CFG, _inv(sender_oib="48879371584")) == "glovo"
    assert resolve_provider(CFG, _inv(sender_oib="38016445738")) is None


def test_external_id_for_wolt_is_the_invoice_number():
    inv = _inv()
    assert extract_external_id("wolt", inv, "") == "90247-2553198637711-2026"


def test_external_id_for_glovo_comes_from_the_line_item():
    inv = _inv(sender_oib="48879371584", invoice_number="2653343/G1/2234278")
    assert extract_external_id("glovo", inv, GLOVO_XML) == "47262-1-5-2026"


def test_external_id_for_glovo_is_none_when_absent():
    inv = _inv(sender_oib="48879371584", invoice_number="361960/G1/2234278")
    assert extract_external_id("glovo", inv, "<Invoice/>") is None


def test_valid_invoice_has_no_issues():
    assert validate_invoice(CFG, _inv(), "wolt", "90247-2553198637711-2026") == []


def test_wolt_drive_is_excluded():
    inv = _inv(invoice_number="270-2553198637741-2026")
    issues = validate_invoice(CFG, inv, "wolt", "270-2553198637741-2026")
    assert any("drive" in i.lower() for i in issues)


def test_credit_note_is_blocked():
    issues = validate_invoice(CFG, _inv(document_type_id=381), "wolt", "x")
    assert any("credit note" in i.lower() for i in issues)


def test_unknown_pizzeria_is_blocked():
    issues = validate_invoice(CFG, _inv(dodois_pizzeria=None), "wolt", "x")
    assert any("pizzeria" in i.lower() for i in issues)


def test_unmapped_pizzeria_is_blocked():
    issues = validate_invoice(CFG, _inv(dodois_pizzeria="Zagreb-9"), "wolt", "x")
    assert any("project" in i.lower() for i in issues)


def test_missing_vat_date_is_blocked():
    issues = validate_invoice(CFG, _inv(vat_date=None), "wolt", "x")
    assert any("vat date" in i.lower() for i in issues)


def test_amount_mismatch_is_blocked():
    issues = validate_invoice(CFG, _inv(total_vat=1.0), "wolt", "x")
    assert any("amount" in i.lower() for i in issues)


def test_missing_external_id_is_blocked():
    issues = validate_invoice(CFG, _inv(), "wolt", None)
    assert any("external id" in i.lower() for i in issues)


def test_unknown_provider_is_blocked():
    issues = validate_invoice(CFG, _inv(sender_oib="1"), None, "x")
    assert any("provider" in i.lower() for i in issues)


def test_select_candidates_skips_posted_and_deleted(session):
    session.add(_inv(document_nr="a", invoice_number="a"))
    session.add(_inv(document_nr="b", invoice_number="b",
                     planfact_operation_id="123"))
    session.add(_inv(document_nr="c", invoice_number="c",
                     processing_status="deleted"))
    session.add(_inv(document_nr="d", invoice_number="d",
                     sender_oib="38016445738", sender_name="METRO"))
    session.commit()

    got = [i.invoice_number for i in select_candidates(session, CFG)]
    assert got == ["a"]
