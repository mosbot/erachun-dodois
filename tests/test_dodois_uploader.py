import pytest
from app.core.ubl_parser import UBLLineItem
from app.core.dodois_uploader import validate_invoice
from tests.conftest import make_invoice, make_ubl


def test_validate_ok(session, metro_mapping, jalapeno_pm):
    inv = make_invoice()
    ubl = make_ubl([
        UBLLineItem(item_name="JALAPENO", quantity=10, line_total=50.0,
                    tax_percent=25, tax_amount=12.5),
    ])
    issues = validate_invoice(session, inv, ubl)
    assert issues == []


def test_validate_supplier_not_enabled(session):
    inv = make_invoice()
    ubl = make_ubl([])
    issues = validate_invoice(session, inv, ubl)
    assert len(issues) == 1
    assert "поставщик" in issues[0].lower()


def test_validate_no_pizzeria(session, metro_mapping):
    inv = make_invoice(pizzeria=None)
    ubl = make_ubl([])
    issues = validate_invoice(session, inv, ubl)
    assert any("пиццерия" in i.lower() for i in issues)


def test_validate_unmapped_product(session, metro_mapping):
    inv = make_invoice()
    ubl = make_ubl([
        UBLLineItem(item_name="Unknown product XYZ", quantity=1, line_total=10.0),
    ])
    issues = validate_invoice(session, inv, ubl)
    assert any("маппинга" in i.lower() for i in issues)


def test_validate_reports_unmapped_name(session, metro_mapping):
    inv = make_invoice()
    ubl = make_ubl([
        UBLLineItem(item_name="Mysterious cheese 2kg", quantity=2, line_total=20.0),
    ])
    issues = validate_invoice(session, inv, ubl)
    assert any("Mysterious cheese 2kg" in i for i in issues)
