# Dodois Upload Mechanism — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual invoice upload button to the invoice detail panel, a Dodois status column to the invoice table, and the backend logic to validate and post invoices to the Dodois REST API.

**Architecture:** New `app/core/dodois_uploader.py` handles all upload logic (validate, build payload, call API). `app/web/app.py` gains a Dodois status column in the table and a `render_dodois_upload_block` function in the detail panel. The pizzeria selector moves from its current standalone position into that block.

**Tech Stack:** Python 3.12, SQLAlchemy ORM, Streamlit, existing `DodoisClient` + `DodoisSession`, `UBLInvoice` from `ubl_parser.py`.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `app/core/dodois_uploader.py` | **Create** | Validate invoice readiness, build Dodois payload, call API |
| `tests/test_dodois_uploader.py` | **Create** | Unit tests for all uploader functions |
| `tests/conftest.py` | **Create** | SQLite in-memory session fixture |
| `app/web/app.py` | **Modify** | Dodois column in table + upload block in detail panel |

---

## Task 1: Test infrastructure + `validate_invoice`

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_dodois_uploader.py`
- Create: `app/core/dodois_uploader.py`

- [ ] **Step 1.1: Install pytest**

```bash
pip install pytest
```

- [ ] **Step 1.2: Write failing tests for `validate_invoice`**

Create `tests/conftest.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.models import (
    Base, SupplierMapping, DodoisSupplierCatalog,
    DodoisRawMaterialCatalog, ProductMapping, Invoice,
)
from app.core.ubl_parser import UBLInvoice, UBLLineItem
from datetime import datetime


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


@pytest.fixture
def metro_catalog(session):
    cat = DodoisSupplierCatalog(
        dodois_id="supplier-metro",
        dodois_name="METRO",
        dodois_inn="38016445738",
    )
    session.add(cat)
    session.flush()
    return cat


@pytest.fixture
def metro_mapping(session, metro_catalog):
    mapping = SupplierMapping(
        eracun_oib="38016445738",
        eracun_name="METRO Cash & Carry",
        dodois_catalog_id=metro_catalog.id,
        enabled=True,
    )
    session.add(mapping)
    session.flush()
    return mapping


@pytest.fixture
def jalapeno_material(session, metro_catalog):
    mat = DodoisRawMaterialCatalog(
        supplier_catalog_id=metro_catalog.id,
        dodois_material_id="mat-jalapeno",
        dodois_container_id="cont-jalapeno",
        dodois_name="Jalapeno 450g",
        unit=5,
        container_size=450.0,
    )
    session.add(mat)
    session.flush()
    return mat


@pytest.fixture
def jalapeno_pm(session, metro_mapping, jalapeno_material):
    pm = ProductMapping(
        supplier_mapping_id=metro_mapping.id,
        eracun_description="JALAPENO",
        dodois_raw_material_id=jalapeno_material.id,
        enabled=True,
    )
    session.add(pm)
    session.flush()
    return pm


def make_invoice(oib="38016445738", pizzeria="Zagreb-1", inv_number="TEST-001"):
    """Create an in-memory Invoice (not persisted to DB) for tests."""
    inv = Invoice(
        electronic_id=None,
        document_nr=inv_number,
        sender_oib=oib,
        sender_name="METRO Cash & Carry",
        invoice_number=inv_number,
        issue_date=datetime(2026, 1, 28),
        dodois_pizzeria=pizzeria,
        processing_status="parsed",
    )
    return inv


def make_ubl(lines):
    ubl = UBLInvoice()
    ubl.invoice_number = "TEST-001"
    ubl.lines = lines
    return ubl
```

Create `tests/test_dodois_uploader.py`:

```python
import pytest
from app.core.dodois_uploader import validate_invoice
from app.core.ubl_parser import UBLLineItem
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
```

- [ ] **Step 1.3: Run tests — expect FAIL (module not found)**

```bash
cd /Users/ask/Projects/erachun-dodois && python -m pytest tests/test_dodois_uploader.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'app.core.dodois_uploader'`

- [ ] **Step 1.4: Create `app/core/dodois_uploader.py` with `validate_invoice`**

```python
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
```

- [ ] **Step 1.5: Run tests — expect PASS**

```bash
python -m pytest tests/test_dodois_uploader.py -v
```

Expected:
```
PASSED tests/test_dodois_uploader.py::test_validate_ok
PASSED tests/test_dodois_uploader.py::test_validate_supplier_not_enabled
PASSED tests/test_dodois_uploader.py::test_validate_no_pizzeria
PASSED tests/test_dodois_uploader.py::test_validate_unmapped_product
PASSED tests/test_dodois_uploader.py::test_validate_reports_unmapped_name
```

- [ ] **Step 1.6: Commit**

```bash
git add app/core/dodois_uploader.py tests/conftest.py tests/test_dodois_uploader.py
git commit -m "feat: add validate_invoice + test infrastructure"
```

---

## Task 2: `_aggregate_lines`, `_compute_price_per_unit`, `build_supply_payload`

**Files:**
- Modify: `app/core/dodois_uploader.py`
- Modify: `tests/test_dodois_uploader.py`

- [ ] **Step 2.1: Add tests for helpers and `build_supply_payload`**

Append to `tests/test_dodois_uploader.py`:

```python
from datetime import datetime
from types import SimpleNamespace
from app.core.dodois_uploader import (
    _aggregate_lines, _compute_price_per_unit, build_supply_payload,
)
from tests.conftest import make_invoice, make_ubl


# ── _aggregate_lines ────────────────────────────────────────────────────────

def test_aggregate_lines_keeps_unique():
    lines = [
        UBLLineItem(item_name="A", quantity=2, line_total=10.0,
                    tax_percent=25, tax_amount=2.5),
        UBLLineItem(item_name="B", quantity=1, line_total=5.0,
                    tax_percent=13, tax_amount=0.65),
    ]
    result = _aggregate_lines(lines)
    assert len(result) == 2


def test_aggregate_lines_sums_duplicates():
    lines = [
        UBLLineItem(item_name="JALAPENO", quantity=1, line_total=5.22,
                    tax_percent=25, tax_amount=1.305),
        UBLLineItem(item_name="JALAPENO", quantity=1, line_total=5.22,
                    tax_percent=25, tax_amount=1.305),
        UBLLineItem(item_name="JALAPENO", quantity=1, line_total=5.22,
                    tax_percent=25, tax_amount=1.305),
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


# ── _compute_price_per_unit ─────────────────────────────────────────────────

def test_price_per_unit_grams():
    # Jalapeno 450g, 18 pcs, €46.98 without VAT
    # divisor = 18 * 450 / 1000 = 8.1
    # pricePerUnit = 46.98 / 8.1 = 5.8
    mat = SimpleNamespace(dodois_container_id="c", unit=5, container_size=450.0)
    assert _compute_price_per_unit(46.98, 18, mat) == 5.8


def test_price_per_unit_pcs_with_container():
    # Napkins 500pcs container, 2 packages, €10.0
    # divisor = 2 * 500 = 1000
    # pricePerUnit = 10.0 / 1000 = 0.01
    mat = SimpleNamespace(dodois_container_id="c", unit=1, container_size=500.0)
    assert _compute_price_per_unit(10.0, 2, mat) == 0.01


def test_price_per_unit_no_container():
    # Vindi Sok 0.25L (no container), 24 pcs, €24.0
    # divisor = 24
    # pricePerUnit = 24.0 / 24 = 1.0
    mat = SimpleNamespace(dodois_container_id=None, unit=1, container_size=1.0)
    assert _compute_price_per_unit(24.0, 24, mat) == 1.0


def test_price_per_unit_meters():
    # Baking paper 8m container, 3 rolls, €12.0
    # divisor = 3 * 8 = 24
    # pricePerUnit = 12.0 / 24 = 0.5
    mat = SimpleNamespace(dodois_container_id="c", unit=8, container_size=8.0)
    assert _compute_price_per_unit(12.0, 3, mat) == 0.5


# ── build_supply_payload ────────────────────────────────────────────────────

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

    assert "id" in payload and len(payload["id"]) == 32  # uuid.hex
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
    assert item["taxId"] == "c2f84413b0f6bba911ee2c6a71f95c44"  # 25% VAT
    assert item["quantity"] == 18
    assert item["totalPriceWithoutVat"] == 46.98
    assert item["totalPriceWithVat"] == round(46.98 + 11.74, 2)
    assert item["vatValue"] == 11.74
    assert item["pricePerUnitWithoutVat"] == 5.8
    assert item["pricePerUnitWithVat"] == round((46.98 + 11.74) / (18 * 450 / 1000), 2)


def test_build_supply_payload_aggregates_duplicates(session, metro_mapping, metro_catalog, jalapeno_pm, jalapeno_material):
    inv = make_invoice()
    inv.issue_date = datetime(2026, 1, 28)
    # Three duplicate JALAPENO lines (qty=1 each) — should be merged
    ubl = make_ubl([
        UBLLineItem(item_name="JALAPENO", quantity=1, line_total=2.61,
                    tax_percent=25, tax_amount=0.65),
        UBLLineItem(item_name="JALAPENO", quantity=1, line_total=2.61,
                    tax_percent=25, tax_amount=0.65),
        UBLLineItem(item_name="JALAPENO", quantity=1, line_total=2.61,
                    tax_percent=25, tax_amount=0.65),
    ])
    pizzeria_cfg = {"unit_id": "unit-1", "department_id": "dept-1"}
    payload = build_supply_payload(session, inv, ubl, pizzeria_cfg)

    assert len(payload["supplyItems"]) == 1
    assert payload["supplyItems"][0]["quantity"] == 3
```

- [ ] **Step 2.2: Run tests — expect FAIL**

```bash
python -m pytest tests/test_dodois_uploader.py -v -k "aggregate or price_per_unit or build_supply" 2>&1 | tail -15
```

Expected: `ImportError: cannot import name '_aggregate_lines'`

- [ ] **Step 2.3: Add `_aggregate_lines`, `_compute_price_per_unit`, and `build_supply_payload` to `app/core/dodois_uploader.py`**

Append after `validate_invoice`:

```python
from collections import defaultdict
import uuid
from datetime import datetime


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
        from app.core.ubl_parser import UBLLineItem
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
        tax_pct = int(line.tax_percent)
        tax_id = TAX_RATE_IDS.get(tax_pct, TAX_RATE_IDS[25])

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
```

- [ ] **Step 2.4: Run tests — expect PASS**

```bash
python -m pytest tests/test_dodois_uploader.py -v
```

Expected: all 14 tests pass.

- [ ] **Step 2.5: Commit**

```bash
git add app/core/dodois_uploader.py tests/test_dodois_uploader.py
git commit -m "feat: add build_supply_payload with price formula and line aggregation"
```

---

## Task 3: `upload_invoice`

**Files:**
- Modify: `app/core/dodois_uploader.py`
- Modify: `tests/test_dodois_uploader.py`

- [ ] **Step 3.1: Add test for `upload_invoice`**

Append to `tests/test_dodois_uploader.py`:

```python
from unittest.mock import MagicMock
from app.core.dodois_uploader import upload_invoice


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
    mock_client.create_supply.return_value = {"id": "returned-supply-id-123"}

    supply_id = upload_invoice(session, inv, ubl, mock_client, pizzeria_cfg)

    assert supply_id == "returned-supply-id-123"
    assert inv.dodois_supply_id == "returned-supply-id-123"
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
```

- [ ] **Step 3.2: Run tests — expect FAIL**

```bash
python -m pytest tests/test_dodois_uploader.py -v -k "upload_invoice" 2>&1 | tail -10
```

Expected: `ImportError: cannot import name 'upload_invoice'`

- [ ] **Step 3.3: Add `upload_invoice` to `app/core/dodois_uploader.py`**

Append after `build_supply_payload`:

```python
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
```

- [ ] **Step 3.4: Run all tests — expect PASS**

```bash
python -m pytest tests/test_dodois_uploader.py -v
```

Expected: all 16 tests pass.

- [ ] **Step 3.5: Commit**

```bash
git add app/core/dodois_uploader.py tests/test_dodois_uploader.py
git commit -m "feat: add upload_invoice"
```

---

## Task 4: Dodois status column in invoice table

**Files:**
- Modify: `app/web/app.py` — `render_invoices_page` function

- [ ] **Step 4.1: Update `render_invoices_page` to compute and show Dodois column**

In `app/web/app.py`, find the `render_invoices_page` function. Locate the block that builds the `data` list (around line 358). After `session.query(Invoice)` and before building the DataFrame, add a helper to precompute enabled OIBs:

```python
# Precompute which OIBs are enabled for Dodois (one query, not N)
from app.db.models import is_dodois_supplier_enabled
enabled_oibs = set(
    r[0] for r in
    session.query(SupplierMapping.eracun_oib)
    .filter(SupplierMapping.enabled == True, SupplierMapping.dodois_catalog_id.isnot(None))
    .all()
)
```

Then in the `for inv in invoices:` loop, replace the existing dict with:

```python
data.append({
    "Date": inv.issue_date.strftime("%d.%m.%Y") if inv.issue_date else "-",
    "Supplier": inv.sender_name,
    "Invoice #": inv.invoice_number or inv.document_nr,
    "Amount (no VAT)": inv.total_without_vat,
    "VAT": inv.total_vat,
    "Total": inv.total_with_vat,
    "Pizzeria": inv.dodois_pizzeria or "—",
    "Dodois": _dodois_status_label(inv, enabled_oibs),
})
```

Add the helper function above `render_invoices_page` (around line 282):

```python
def _dodois_status_label(inv: Invoice, enabled_oibs: set) -> str:
    """Return a short status string for the Dodois column."""
    if inv.sender_oib not in enabled_oibs:
        return "—"
    if inv.dodois_supply_id:
        return "✓ Загружен"
    if inv.processing_status == "error":
        return "✗ Ошибка"
    return "· Не загружен"
```

In the `st.dataframe(...)` call, add `"Dodois"` to `column_config`:

```python
column_config={
    "Amount (no VAT)": st.column_config.NumberColumn(format="€%.2f"),
    "VAT": st.column_config.NumberColumn(format="€%.2f"),
    "Total": st.column_config.NumberColumn(format="€%.2f"),
    "Dodois": st.column_config.TextColumn(width="small"),
},
```

- [ ] **Step 4.2: Add the missing import at the top of `app.py`**

Find the existing imports block in `app/web/app.py`. Add `SupplierMapping` to the `from app.db.models import (...)` block if not already there (it's already imported — check line ~21).

- [ ] **Step 4.3: Smoke test — start the app and verify column appears**

```bash
cd /Users/ask/Projects/erachun-dodois && streamlit run app/web/app.py
```

Open http://localhost:8501, log in, check that the "Dodois" column appears in the invoice table. METRO invoices should show "✓ Загружен" or "· Не загружен". Other suppliers show "—".

- [ ] **Step 4.4: Commit**

```bash
git add app/web/app.py
git commit -m "feat: add Dodois status column to invoice table"
```

---

## Task 5: Upload block in invoice detail panel

**Files:**
- Modify: `app/web/app.py` — `render_invoices_page`, `render_invoice_detail`, new `render_dodois_upload_block`

- [ ] **Step 5.1: Remove standalone pizzeria selector from `render_invoices_page`**

In `render_invoices_page`, find and delete the block that looks like (starts around line 397):

```python
# Pizzeria selector
cfg = get_config()
pizzerias = cfg.get("dodois", {}).get("pizzerias", {})
pizzeria_names = ["—"] + [v.get("name", k) for k, v in pizzerias.items()]
current = inv.dodois_pizzeria or "—"
current_idx = pizzeria_names.index(current) if current in pizzeria_names else 0

selected_pizzeria = st.selectbox(
    "Pizzeria",
    pizzeria_names,
    index=current_idx,
    key=f"pizzeria_{inv.id}",
)

new_value = None if selected_pizzeria == "—" else selected_pizzeria
if new_value != inv.dodois_pizzeria:
    inv.dodois_pizzeria = new_value
    session.commit()
    st.rerun()
```

- [ ] **Step 5.2: Update `render_invoice_detail` signature to accept session**

Change:
```python
def render_invoice_detail(inv: Invoice):
```
to:
```python
def render_invoice_detail(inv: Invoice, session):
```

Update the two call sites (both in `render_invoices_page`):
```python
render_invoice_detail(inv, session)
```

- [ ] **Step 5.3: Add `render_dodois_upload_block` call inside `render_invoice_detail`**

In `render_invoice_detail`, inside `with col1:`, after the download buttons and before the delete section, add:

```python
cfg = get_config()
render_dodois_upload_block(inv, session, cfg)
```

- [ ] **Step 5.4: Add `render_dodois_upload_block` function**

Add this function above `render_invoice_detail` in `app/web/app.py`:

```python
def render_dodois_upload_block(inv: Invoice, session, cfg: dict):
    """Render the Dodois upload section inside invoice detail."""
    from app.db.models import is_dodois_supplier_enabled, SupplierMapping
    from app.core.dodois_uploader import validate_invoice, upload_invoice
    from app.core.ubl_parser import parse_ubl_xml
    from pathlib import Path

    if not is_dodois_supplier_enabled(session, inv.sender_oib):
        return

    st.divider()

    # ── Already uploaded ─────────────────────────────────────────────────────
    if inv.dodois_supply_id:
        st.markdown(
            f"""<div style="border:1px solid #bbf7d0;border-radius:8px;padding:14px;background:#f0fdf4">
            <b style="color:#15803d">✓ Загружен в Dodois</b><br>
            <span style="font-size:12px;color:#166534">Пиццерия: {inv.dodois_pizzeria or "—"}</span><br>
            <span style="font-size:11px;color:#64748b">ID: {inv.dodois_supply_id[:24]}...</span>
            </div>""",
            unsafe_allow_html=True,
        )
        if st.button("↺ Загрузить повторно", key=f"reupload_{inv.id}", type="secondary",
                     use_container_width=True):
            inv.dodois_supply_id = None
            inv.processing_status = "parsed"
            session.commit()
            st.rerun()
        return

    st.markdown("**Загрузка в Dodois**")

    # ── Pizzeria selector ────────────────────────────────────────────────────
    dodois_cfg = cfg.get("dodois", {})
    pizzerias = dodois_cfg.get("pizzerias", {})
    pizzeria_keys = list(pizzerias.keys())
    pizzeria_names = [v.get("name", k) for k, v in pizzerias.items()]
    current_name = inv.dodois_pizzeria or ""
    current_idx = pizzeria_names.index(current_name) if current_name in pizzeria_names else 0

    selected_name = st.selectbox(
        "Пиццерия",
        pizzeria_names,
        index=current_idx,
        key=f"dodois_pizzeria_{inv.id}",
    )
    if selected_name != inv.dodois_pizzeria:
        inv.dodois_pizzeria = selected_name
        session.commit()
        st.rerun()

    # ── Parse XML and validate ───────────────────────────────────────────────
    storage = get_storage_config(cfg)
    xml_dir = Path(storage.get("xml_dir", "/app/data/xmls"))

    if not inv.xml_path:
        st.warning("XML файл не найден — невозможно проверить маппинг.")
        return

    xml_file = xml_dir / inv.xml_path
    if not xml_file.exists():
        st.warning(f"XML файл не найден на диске: {inv.xml_path}")
        return

    try:
        ubl = parse_ubl_xml(xml_file.read_text(encoding="utf-8"))
    except Exception as e:
        st.error(f"Ошибка парсинга XML: {e}")
        return

    issues = validate_invoice(session, inv, ubl)

    # ── Checklist ────────────────────────────────────────────────────────────
    mapping = session.query(SupplierMapping).filter_by(eracun_oib=inv.sender_oib, enabled=True).first()
    supplier_name = mapping.dodois_supplier.dodois_name if mapping and mapping.dodois_supplier else inv.sender_name
    st.markdown(f"✅ Поставщик настроен ({supplier_name})")

    if inv.dodois_pizzeria:
        st.markdown(f"✅ Пиццерия выбрана ({inv.dodois_pizzeria})")
    else:
        st.markdown("❌ Пиццерия не выбрана")

    if issues:
        for issue in issues:
            if "маппинга" in issue.lower():
                st.markdown(f"❌ {issue}")
                st.caption("→ Маппинги → Товары")
    else:
        st.markdown(f"✅ Все товары замаплены ({len(ubl.lines)} позиций)")

    # ── Upload button ────────────────────────────────────────────────────────
    ready = len(issues) == 0
    if st.button(
        "⬆ Загрузить в Dodois",
        key=f"upload_{inv.id}",
        type="primary",
        disabled=not ready,
        use_container_width=True,
    ):
        from app.core.dodois_auth import DodoisSession
        from app.core.dodois_client import DodoisClient

        dodois_creds = cfg.get("dodois", {})
        selected_key = pizzeria_keys[pizzeria_names.index(selected_name)]
        pizzeria_cfg = pizzerias[selected_key]

        with st.spinner("Загружаю в Dodois..."):
            try:
                ds = DodoisSession(
                    dodois_creds["username"],
                    dodois_creds["password"],
                    dodois_creds.get("totp_secret", ""),
                )
                client = DodoisClient(ds)
                supply_id = upload_invoice(session, inv, ubl, client, pizzeria_cfg)
                st.success(f"Загружено! ID поставки: {supply_id[:24]}...")
                st.rerun()
            except Exception as e:
                inv.processing_status = "error"
                inv.processing_error = str(e)
                session.commit()
                st.error(f"Ошибка загрузки: {e}")
```

- [ ] **Step 5.5: Smoke test — verify upload block appears**

```bash
streamlit run app/web/app.py
```

Open http://localhost:8501, select a METRO invoice. Verify:
- Block "Загрузка в Dodois" appears in left column
- Pizzeria selector shows Zagreb-1 / Zagreb-2
- Checklist shows green/red items
- Button is disabled if there are unmapped products
- Button is active if all items are mapped

- [ ] **Step 5.6: Commit**

```bash
git add app/web/app.py
git commit -m "feat: add Dodois upload block in invoice detail panel"
```

---

## Self-Review Checklist

- [x] `validate_invoice` — tested, blocks on unmapped products ✓
- [x] `build_supply_payload` — tested, handles aggregation, price formula, tax IDs ✓
- [x] `upload_invoice` — tested with mock client ✓
- [x] Dodois column in table — 4 states: —, ✓ Загружен, ✗ Ошибка, · Не загружен ✓
- [x] Upload block: 3 UI states (blocked / ready / uploaded) ✓
- [x] Pizzeria selector moved from standalone to inside upload block ✓
- [x] `render_invoice_detail` receives session ✓
- [x] No DB schema changes ✓
- [x] No new pip dependencies (pytest only for tests) ✓
