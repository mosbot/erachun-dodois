from datetime import datetime
from unittest.mock import MagicMock

from app.core.planfact_poster import (
    resolve_provider, extract_external_id, validate_invoice, select_candidates,
    build_payload, find_existing_operation, post_invoice,
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


def test_payload_amounts_and_categories():
    p = build_payload(CFG, _inv(), "wolt", "90247-2553198637711-2026")
    assert p["accountId"] == 666927
    assert p["value"] == 796.14
    assert p["externalId"] == "90247-2553198637711-2026"
    assert p["isCommitted"] is True
    assert p["operationDate"] == "2026-07-10T00:00:00Z"

    commission, vat = p["items"]
    assert commission["operationCategoryId"] == 8563181
    assert commission["value"] == 636.91
    assert commission["projectId"] == 1172400
    assert commission["calculationDate"] == "2026-07-10T00:00:00Z"
    assert commission["isCalculationCommitted"] is True
    assert vat["operationCategoryId"] == 9485374
    assert vat["value"] == 159.23


def test_payload_uses_glovo_account_and_category():
    inv = _inv(sender_oib="48879371584", dodois_pizzeria="Zagreb-2")
    p = build_payload(CFG, inv, "glovo", "47262-1-5-2026")
    assert p["accountId"] == 666928
    assert p["items"][0]["operationCategoryId"] == 8563431
    assert p["items"][0]["projectId"] == 1198217


def test_payload_comment_carries_the_marker():
    p = build_payload(CFG, _inv(), "wolt", "90247-2553198637711-2026")
    assert p["comment"] == "#erachun Wolt 90247-2553198637711-2026"


def test_find_existing_operation_matches_external_id():
    client = MagicMock()
    client.list_operations.return_value = [
        {"operationId": 111, "externalId": "other"},
        {"operationId": 222, "externalId": "90247-2553198637711-2026"},
    ]
    got = find_existing_operation(client, CFG, _inv(), "wolt",
                                  "90247-2553198637711-2026")
    assert got == "222"
    args, kwargs = client.list_operations.call_args
    assert args[0] == 666927
    assert args[1] == "2026-07-07"     # vat_date - 3 days
    assert args[2] == "2026-07-13"     # vat_date + 3 days


def test_find_existing_operation_returns_none_when_absent():
    client = MagicMock()
    client.list_operations.return_value = [{"operationId": 1, "externalId": "z"}]
    assert find_existing_operation(client, CFG, _inv(), "wolt", "x") is None


def test_post_invoice_skips_when_already_in_planfact():
    client = MagicMock()
    client.list_operations.return_value = [
        {"operationId": 999, "externalId": "90247-2553198637711-2026"}]
    op_id, err = post_invoice(client, CFG, _inv(), "")
    assert op_id == "999"
    assert err is None
    client.create_outcome.assert_not_called()


def test_post_invoice_creates_and_returns_id():
    client = MagicMock()
    client.list_operations.return_value = []
    client.create_outcome.return_value = {"data": {"operationId": 555}}
    op_id, err = post_invoice(client, CFG, _inv(), "")
    assert op_id == "555"
    assert err is None
    client.create_outcome.assert_called_once()


def test_post_invoice_falls_back_to_lookup_on_empty_body():
    """A successful POST may answer with no body; find the id afterwards."""
    client = MagicMock()
    client.list_operations.side_effect = [
        [],
        [{"operationId": 777, "externalId": "90247-2553198637711-2026"}],
    ]
    client.create_outcome.return_value = {}
    op_id, err = post_invoice(client, CFG, _inv(), "")
    assert op_id == "777"
    assert err is None


def test_post_invoice_reports_validation_failure():
    client = MagicMock()
    op_id, err = post_invoice(client, CFG, _inv(dodois_pizzeria=None), "")
    assert op_id is None
    assert "pizzeria" in err.lower()
    client.create_outcome.assert_not_called()


def test_dry_run_never_posts():
    client = MagicMock()
    client.list_operations.return_value = []
    op_id, err = post_invoice(client, CFG, _inv(), "", dry_run=True)
    assert op_id is None
    assert err is None
    client.create_outcome.assert_not_called()


def test_post_invoice_catches_planfact_error_from_list_operations():
    """PlanfactError from client.list_operations is caught and returned as error."""
    from app.core.planfact_client import PlanfactError
    client = MagicMock()
    client.list_operations.side_effect = PlanfactError("API returned 500")
    op_id, err = post_invoice(client, CFG, _inv(), "")
    assert op_id is None
    assert "API returned 500" in err
    client.create_outcome.assert_not_called()


def test_post_invoice_catches_unexpected_exception_from_create_outcome():
    """Unexpected exceptions from client.create_outcome are caught."""
    client = MagicMock()
    client.list_operations.return_value = []
    client.create_outcome.side_effect = ValueError("bad json")
    op_id, err = post_invoice(client, CFG, _inv(), "")
    assert op_id is None
    assert "Unexpected error" in err
    assert "bad json" in err


def test_validate_invoice_checks_account_configured():
    """Missing account entry for a resolved provider is a blocking issue."""
    cfg_no_account = {
        "planfact": {
            "accounts": {"glovo": 666928},  # missing "wolt"
            "projects": {"Zagreb-1": 1172400},
            "categories": {"wolt_commission": 8563181, "glovo_commission": 8563431, "vat": 9485374},
            "providers": {"wolt": {"oib": "25531986377"}},
        }
    }
    issues = validate_invoice(cfg_no_account, _inv(), "wolt", "90247-2553198637711-2026")
    assert any("account" in i.lower() for i in issues)


def test_validate_invoice_checks_commission_category_configured():
    """Missing commission category for a resolved provider is a blocking issue."""
    cfg_no_cat = {
        "planfact": {
            "accounts": {"wolt": 666927},
            "projects": {"Zagreb-1": 1172400},
            "categories": {"glovo_commission": 8563431, "vat": 9485374},  # missing "wolt_commission"
            "providers": {"wolt": {"oib": "25531986377"}},
        }
    }
    issues = validate_invoice(cfg_no_cat, _inv(), "wolt", "90247-2553198637711-2026")
    assert any("commission" in i.lower() for i in issues)


def test_validate_invoice_checks_vat_category_configured():
    """Missing VAT category is a blocking issue."""
    cfg_no_vat = {
        "planfact": {
            "accounts": {"wolt": 666927},
            "projects": {"Zagreb-1": 1172400},
            "categories": {"wolt_commission": 8563181, "glovo_commission": 8563431},  # missing "vat"
            "providers": {"wolt": {"oib": "25531986377"}},
        }
    }
    issues = validate_invoice(cfg_no_vat, _inv(), "wolt", "90247-2553198637711-2026")
    assert any("vat" in i.lower() for i in issues)
