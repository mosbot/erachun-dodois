import pytest
from datetime import datetime
from types import SimpleNamespace
from app.core.ubl_parser import UBLLineItem
from unittest.mock import MagicMock
from app.core.dodois_uploader import (
    validate_invoice, _aggregate_lines, _compute_price_per_unit,
    build_supply_payload, upload_invoice,
)
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


# ── _aggregate_lines ─────────────────────────────────────────────────────────

def test_aggregate_lines_keeps_unique():
    lines = [
        UBLLineItem(item_name="A", quantity=2, line_total=10.0, tax_percent=25, tax_amount=2.5),
        UBLLineItem(item_name="B", quantity=1, line_total=5.0, tax_percent=13, tax_amount=0.65),
    ]
    result = _aggregate_lines(lines)
    assert len(result) == 2


def test_aggregate_lines_sums_duplicates():
    lines = [
        UBLLineItem(item_name="JALAPENO", quantity=1, line_total=5.22, tax_percent=25, tax_amount=1.305),
        UBLLineItem(item_name="JALAPENO", quantity=1, line_total=5.22, tax_percent=25, tax_amount=1.305),
        UBLLineItem(item_name="JALAPENO", quantity=1, line_total=5.22, tax_percent=25, tax_amount=1.305),
    ]
    result = _aggregate_lines(lines)
    assert len(result) == 1
    assert result[0].quantity == 3
    assert round(result[0].line_total, 2) == 15.66
    assert round(result[0].tax_amount, 3) == 3.915


def test_aggregate_lines_skips_empty_name():
    lines = [UBLLineItem(item_name="", description="", quantity=1, line_total=1.0)]
    result = _aggregate_lines(lines)
    assert result == []


# ── _compute_price_per_unit ──────────────────────────────────────────────────

def test_price_per_unit_grams():
    # Jalapeno 450g, 18 pcs, €46.98 — divisor = 18 * 450 / 1000 = 8.1
    mat = SimpleNamespace(dodois_container_id="c", unit=5, container_size=450.0)
    assert _compute_price_per_unit(46.98, 18, mat) == 5.8


def test_price_per_unit_pcs_with_container():
    # Napkins 500pcs container, 2 packages, €10.0 — divisor = 2 * 500 = 1000
    mat = SimpleNamespace(dodois_container_id="c", unit=1, container_size=500.0)
    assert _compute_price_per_unit(10.0, 2, mat) == 0.01


def test_price_per_unit_no_container():
    # Vindi Sok (no container), 24 pcs, €24.0 — divisor = 24
    mat = SimpleNamespace(dodois_container_id=None, unit=1, container_size=1.0)
    assert _compute_price_per_unit(24.0, 24, mat) == 1.0


def test_price_per_unit_meters():
    # Baking paper 8m, 3 rolls, €12.0 — divisor = 3 * 8 = 24
    mat = SimpleNamespace(dodois_container_id="c", unit=8, container_size=8.0)
    assert _compute_price_per_unit(12.0, 3, mat) == 0.5


# ── build_supply_payload ─────────────────────────────────────────────────────

def test_build_supply_payload_structure(session, metro_mapping, metro_catalog, jalapeno_pm, jalapeno_material):
    inv = make_invoice()
    inv.invoice_number = "2315/11/6005"
    inv.issue_date = datetime(2026, 1, 28)
    ubl = make_ubl([
        UBLLineItem(item_name="JALAPENO", quantity=18, line_total=46.98,
                    tax_percent=25, tax_amount=11.74),
    ])
    pizzeria_cfg = {"unit_id": "unit-zagreb-1", "department_id": "dept-1"}
    payload = build_supply_payload(session, inv, ubl, pizzeria_cfg)

    assert "id" in payload and len(payload["id"]) == 32
    assert payload["invoiceNumber"] == "2315/11/6005"
    assert payload["commercialInvoiceNumber"] == "2315/11/6005"
    assert payload["invoiceDate"] == "2026-01-28"
    assert payload["commercialInvoiceDate"] == "2026-01-28"
    assert payload["unitId"] == "unit-zagreb-1"
    assert payload["supplierId"] == "supplier-metro"
    assert payload["hasVat"] is True
    assert payload["currencyCode"] == 978

    assert len(payload["supplyItems"]) == 1
    item = payload["supplyItems"][0]
    assert item["rawMaterialId"] == "mat-jalapeno"
    assert item["rawMaterialContainerId"] == "cont-jalapeno"
    assert item["taxId"] == "c2f84413b0f6bba911ee2c6a71f95c44"
    assert item["quantity"] == 18
    assert item["totalPriceWithoutVat"] == 46.98
    assert item["totalPriceWithVat"] == round(46.98 + 11.74, 2)
    assert item["vatValue"] == 11.74
    assert item["pricePerUnitWithoutVat"] == 5.8
    assert item["pricePerUnitWithVat"] == round((46.98 + 11.74) / (18 * 450 / 1000), 2)


def test_build_supply_payload_aggregates_duplicates(session, metro_mapping, metro_catalog, jalapeno_pm, jalapeno_material):
    inv = make_invoice()
    inv.issue_date = datetime(2026, 1, 28)
    ubl = make_ubl([
        UBLLineItem(item_name="JALAPENO", quantity=1, line_total=2.61, tax_percent=25, tax_amount=0.65),
        UBLLineItem(item_name="JALAPENO", quantity=1, line_total=2.61, tax_percent=25, tax_amount=0.65),
        UBLLineItem(item_name="JALAPENO", quantity=1, line_total=2.61, tax_percent=25, tax_amount=0.65),
    ])
    pizzeria_cfg = {"unit_id": "unit-1", "department_id": "dept-1"}
    payload = build_supply_payload(session, inv, ubl, pizzeria_cfg)

    assert len(payload["supplyItems"]) == 1
    assert payload["supplyItems"][0]["quantity"] == 3


# ── upload_invoice ────────────────────────────────────────────────────────────

def test_upload_invoice_sets_supply_id(session, metro_mapping, metro_catalog, jalapeno_pm, jalapeno_material):
    inv = make_invoice()
    inv.issue_date = datetime(2026, 1, 28)
    session.add(inv)
    session.flush()

    ubl = make_ubl([
        UBLLineItem(item_name="JALAPENO", quantity=5, line_total=25.0,
                    tax_percent=25, tax_amount=6.25),
    ])
    pizzeria_cfg = {"unit_id": "unit-1", "department_id": "dept-1"}
    mock_client = MagicMock()
    mock_client.create_supply.return_value = {"id": "returned-supply-id-abc123"}

    supply_id = upload_invoice(session, inv, ubl, mock_client, pizzeria_cfg)

    assert supply_id == "returned-supply-id-abc123"
    assert inv.dodois_supply_id == "returned-supply-id-abc123"
    assert inv.processing_status == "uploaded_to_dodois"
    mock_client.create_supply.assert_called_once()


def test_upload_invoice_raises_on_api_error(session, metro_mapping, metro_catalog, jalapeno_pm, jalapeno_material):
    inv = make_invoice()
    inv.issue_date = datetime(2026, 1, 28)
    session.add(inv)
    session.flush()

    ubl = make_ubl([
        UBLLineItem(item_name="JALAPENO", quantity=1, line_total=5.0,
                    tax_percent=25, tax_amount=1.25),
    ])
    pizzeria_cfg = {"unit_id": "unit-1", "department_id": "dept-1"}
    mock_client = MagicMock()
    mock_client.create_supply.side_effect = Exception("API error 422")

    with pytest.raises(Exception, match="API error 422"):
        upload_invoice(session, inv, ubl, mock_client, pizzeria_cfg)
