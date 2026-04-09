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


def test_price_per_unit_weighed_no_container():
    # Pivac ham (no container, unit=5), 30.176 kg → 30176 g, €129.76
    # Price per kg = 129.76 / 30.176 ≈ 4.30
    mat = SimpleNamespace(dodois_container_id=None, unit=5, container_size=1.0)
    assert _compute_price_per_unit(129.76, 30176, mat) == 4.3


def test_price_per_unit_meters():
    # Baking paper 8m, 3 rolls, €12.0 — divisor = 3 * 8 = 24
    mat = SimpleNamespace(dodois_container_id="c", unit=8, container_size=8.0)
    assert _compute_price_per_unit(12.0, 3, mat) == 0.5


def test_price_per_unit_half_cent_rounds_half_up():
    # Regression: invoice 2810-1-1 (Jana/Jamnica beverages) got rejected with
    # SUPPLY_ITEM_PRICE_PER_UNIT_WITH_VAT_WRONG_CALCULATION because Python's
    # round() gives 1.07 for 1.075 (float 1.0749999…) while the Dodois .NET
    # server rounds HALF_UP to 1.08.
    #
    # Jamnica 0,5L 12-pack: totalWithVat 12.90 / (1 × 12) = 1.075 → must be 1.08
    mat = SimpleNamespace(dodois_container_id="c", unit=1, container_size=12.0)
    assert _compute_price_per_unit(12.90, 1, mat) == 1.08
    # Jana Ice Tea 0,5L 6-pack: 5.85 / 6 = 0.975 → must be 0.98
    mat6 = SimpleNamespace(dodois_container_id="c", unit=1, container_size=6.0)
    assert _compute_price_per_unit(5.85, 1, mat6) == 0.98


# ── build_supply_payload ─────────────────────────────────────────────────────

def test_build_supply_payload_derives_price_with_vat_from_formula(
    session, metro_mapping, metro_catalog
):
    """Regression for invoice 2810-1-1 Jana/Jamnica: pricePerUnitWithVat must
    be derived from pricePerUnitWithoutVat × (1 + taxRate) using HALF_UP
    rounding, matching the Dodois server validation formula.
    """
    from app.db.models import DodoisRawMaterialCatalog, ProductMapping

    # Jamnica 0,5 Narančada PVC (12) — unit=1 pcs, 12-bottle pack
    mat = DodoisRawMaterialCatalog(
        supplier_catalog_id=metro_catalog.id,
        dodois_material_id="mat-jamnica",
        dodois_container_id="cont-jamnica",
        dodois_name="Jamnica 0,5 Narancada PVC (12)",
        unit=1,
        container_size=12.0,
    )
    session.add(mat)
    session.flush()
    session.add(ProductMapping(
        supplier_mapping_id=metro_mapping.id,
        eracun_description="JAMNICA NARANCADA 0.5L",
        dodois_raw_material_id=mat.id,
        enabled=True,
    ))
    session.flush()

    inv = make_invoice()
    ubl = make_ubl([
        UBLLineItem(
            item_name="JAMNICA NARANCADA 0.5L",
            quantity=1, line_total=10.32,
            tax_percent=25, tax_amount=2.58,
        ),
    ])
    pizzeria_cfg = {"unit_id": "unit-1", "department_id": "dept-1"}
    payload, _ = build_supply_payload(session, inv, ubl, pizzeria_cfg)
    item = payload["supplyItems"][0]
    assert item["pricePerUnitWithoutVat"] == 0.86
    # Server expects 0.86 × 1.25 = 1.075 → HALF_UP → 1.08 (not Python's 1.07).
    assert item["pricePerUnitWithVat"] == 1.08


def test_build_supply_payload_structure(session, metro_mapping, metro_catalog, jalapeno_pm, jalapeno_material):
    inv = make_invoice()
    inv.invoice_number = "2315/11/6005"
    inv.issue_date = datetime(2026, 1, 28)
    ubl = make_ubl([
        UBLLineItem(item_name="JALAPENO", quantity=18, line_total=46.98,
                    tax_percent=25, tax_amount=11.74),
    ])
    pizzeria_cfg = {"unit_id": "unit-zagreb-1", "department_id": "dept-1"}
    payload, skipped = build_supply_payload(session, inv, ubl, pizzeria_cfg)

    assert skipped == []
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
    payload, skipped = build_supply_payload(session, inv, ubl, pizzeria_cfg)

    assert skipped == []
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

    supply_id, skipped = upload_invoice(session, inv, ubl, mock_client, pizzeria_cfg)

    assert supply_id == "returned-supply-id-abc123"
    assert skipped == []
    assert inv.dodois_supply_id == "returned-supply-id-abc123"
    assert inv.dodois_upload_partial is False
    assert inv.dodois_skipped_count == 0
    assert inv.dodois_skipped_lines is None
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


# ── Partial upload (skip_unmapped) ────────────────────────────────────────────

def test_build_supply_payload_unmapped_raises_by_default(
    session, metro_mapping, metro_catalog, jalapeno_pm, jalapeno_material
):
    inv = make_invoice()
    ubl = make_ubl([
        UBLLineItem(item_name="JALAPENO", quantity=1, line_total=5.0,
                    tax_percent=25, tax_amount=1.25),
        UBLLineItem(item_name="UnknownThing", quantity=2, line_total=10.0,
                    tax_percent=25, tax_amount=2.5),
    ])
    pizzeria_cfg = {"unit_id": "unit-1", "department_id": "dept-1"}
    with pytest.raises(ValueError, match="No mapping for line: UnknownThing"):
        build_supply_payload(session, inv, ubl, pizzeria_cfg)


def test_build_supply_payload_skip_unmapped(
    session, metro_mapping, metro_catalog, jalapeno_pm, jalapeno_material
):
    inv = make_invoice()
    ubl = make_ubl([
        UBLLineItem(item_name="JALAPENO", quantity=18, line_total=46.98,
                    tax_percent=25, tax_amount=11.74),
        UBLLineItem(item_name="Mystery widget", quantity=2, line_total=10.0,
                    tax_percent=25, tax_amount=2.5),
        UBLLineItem(item_name="Another unknown", quantity=1, line_total=3.0,
                    tax_percent=25, tax_amount=0.75),
    ])
    pizzeria_cfg = {"unit_id": "unit-1", "department_id": "dept-1"}
    payload, skipped = build_supply_payload(
        session, inv, ubl, pizzeria_cfg, skip_unmapped=True,
    )

    assert len(payload["supplyItems"]) == 1
    assert payload["supplyItems"][0]["rawMaterialId"] == "mat-jalapeno"
    assert skipped == ["Mystery widget", "Another unknown"]


def test_build_supply_payload_empty_after_skip_raises(
    session, metro_mapping, metro_catalog
):
    inv = make_invoice()
    ubl = make_ubl([
        UBLLineItem(item_name="Nothing mapped", quantity=1, line_total=5.0,
                    tax_percent=25, tax_amount=1.25),
    ])
    pizzeria_cfg = {"unit_id": "unit-1", "department_id": "dept-1"}
    with pytest.raises(ValueError, match="No mapped lines to upload"):
        build_supply_payload(
            session, inv, ubl, pizzeria_cfg, skip_unmapped=True,
        )


def test_upload_invoice_partial_persists_skipped(
    session, metro_mapping, metro_catalog, jalapeno_pm, jalapeno_material
):
    import json as _json
    inv = make_invoice()
    inv.issue_date = datetime(2026, 1, 28)
    session.add(inv)
    session.flush()

    ubl = make_ubl([
        UBLLineItem(item_name="JALAPENO", quantity=5, line_total=25.0,
                    tax_percent=25, tax_amount=6.25),
        UBLLineItem(item_name="Ghost product", quantity=1, line_total=1.0,
                    tax_percent=25, tax_amount=0.25),
    ])
    pizzeria_cfg = {"unit_id": "unit-1", "department_id": "dept-1"}
    mock_client = MagicMock()
    mock_client.create_supply.return_value = {"id": "supply-partial-xyz"}

    supply_id, skipped = upload_invoice(
        session, inv, ubl, mock_client, pizzeria_cfg, skip_unmapped=True,
    )

    assert supply_id == "supply-partial-xyz"
    assert skipped == ["Ghost product"]
    assert inv.dodois_upload_partial is True
    assert inv.dodois_skipped_count == 1
    assert _json.loads(inv.dodois_skipped_lines) == ["Ghost product"]
    assert inv.processing_status == "uploaded_to_dodois"
