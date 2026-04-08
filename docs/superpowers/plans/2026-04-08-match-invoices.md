# Product Mapping via Invoice Cross-Reference — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Автоматически заполнить `ProductMapping` (eRačun description → Dodois rawMaterialId) путём кросс-матчинга 399 eRačun инвойсов с поставками из Dodois для всех поставщиков.

**Architecture:** Скрипт `scripts/match_invoices.py` тянет все Dodois поставки через API (оба деп-та, пагинация), конвертирует номера инвойсов, находит пары в eRačun DB, парсит XML чтобы получить строки с описаниями, матчит по qty+price с Dodois items, пишет `ProductMapping`.

**Tech Stack:** Python 3.12, SQLAlchemy, requests, app.core.dodois_client (расширяем), app.core.ubl_parser (as-is), pytest

---

## File Map

| Файл | Действие | Ответственность |
|------|----------|-----------------|
| `app/core/dodois_client.py` | Modify | Добавить `get_all_supplies(dept_id, from_date, to_date)` + `get_supply_detail(supply_id)` |
| `scripts/match_invoices.py` | Create | Основной скрипт: fetch → match invoices → match lines → write DB |
| `tests/test_match_invoices.py` | Create | Unit-тесты: конвертация номеров, агрегация строк, матчинг строк |

---

## Task 1: Extend DodoisClient — supply fetching methods

**Files:**
- Modify: `app/core/dodois_client.py`
- Test: `tests/test_match_invoices.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_match_invoices.py`:

```python
"""Tests for invoice matching logic."""
import pytest
from scripts.match_invoices import dodois_to_eracun, aggregate_ubl_lines, match_lines


class TestDodoisToEracun:
    def test_standard_format(self):
        assert dodois_to_eracun("6/0(011)0003/004488") == "4488/11/6003"

    def test_zero_prefix(self):
        assert dodois_to_eracun("0/0(010)0001/003895") == "3895/10/0001"

    def test_different_seq(self):
        assert dodois_to_eracun("6/0(010)0005/001729") == "1729/10/6005"

    def test_already_eracun_format(self):
        # "2315/11/6005" is already eRačun format — return unchanged
        assert dodois_to_eracun("2315/11/6005") == "2315/11/6005"

    def test_invalid_returns_unchanged(self):
        assert dodois_to_eracun("INVALID") == "INVALID"


class TestAggregateUblLines:
    def _make_line(self, description, quantity, line_total):
        """Helper: create a dict line (same format match_lines expects)."""
        return {"description": description, "quantity": quantity, "line_total": line_total}

    def test_no_duplicates_unchanged(self):
        lines = [
            self._make_line("PRODUCT A", 2.0, 10.00),
            self._make_line("PRODUCT B", 1.0, 5.00),
        ]
        result = aggregate_ubl_lines(lines)
        assert len(result) == 2

    def test_duplicates_merged(self):
        lines = [
            self._make_line("PRODUCT A", 1.0, 5.00),
            self._make_line("PRODUCT A", 1.0, 5.00),
        ]
        result = aggregate_ubl_lines(lines)
        assert len(result) == 1
        assert result[0]["quantity"] == 2.0
        assert abs(result[0]["line_total"] - 10.00) < 0.001

    def test_empty_description_skipped(self):
        lines = [
            self._make_line("", 1.0, 5.00),
            self._make_line("PRODUCT A", 2.0, 10.00),
        ]
        result = aggregate_ubl_lines(lines)
        assert len(result) == 1
        assert result[0]["description"] == "PRODUCT A"


class TestMatchLines:
    def test_exact_match(self):
        ubl_lines = [
            {"description": "JALAPENO 450G", "quantity": 6.0, "line_total": 46.98},
            {"description": "CHEDDAR 1KG", "quantity": 2.0, "line_total": 20.00},
        ]
        dodois_items = [
            {"rawMaterialId": "mat-a", "containerId": "con-a", "qty": 6.0, "totalWithVat": 46.98},
        ]
        result = match_lines(ubl_lines, dodois_items)
        assert len(result) == 1
        assert result[0]["description"] == "JALAPENO 450G"
        assert result[0]["rawMaterialId"] == "mat-a"

    def test_price_tolerance(self):
        ubl_lines = [
            {"description": "PRODUCT A", "quantity": 1.0, "line_total": 10.01},
        ]
        dodois_items = [
            {"rawMaterialId": "mat-a", "containerId": "con-a", "qty": 1.0, "totalWithVat": 10.00},
        ]
        result = match_lines(ubl_lines, dodois_items)
        assert len(result) == 1

    def test_ambiguous_skipped(self):
        ubl_lines = [
            {"description": "PRODUCT A", "quantity": 2.0, "line_total": 20.00},
            {"description": "PRODUCT B", "quantity": 2.0, "line_total": 20.00},
        ]
        dodois_items = [
            {"rawMaterialId": "mat-a", "containerId": "con-a", "qty": 2.0, "totalWithVat": 20.00},
        ]
        result = match_lines(ubl_lines, dodois_items)
        assert len(result) == 0  # ambiguous → skipped

    def test_aggregate_then_match(self):
        # METRO sends same product twice with qty=1 each → aggregate first, then match
        raw_lines = [
            {"description": "PRODUCT A", "quantity": 1.0, "line_total": 5.00},
            {"description": "PRODUCT A", "quantity": 1.0, "line_total": 5.00},
        ]
        aggregated = aggregate_ubl_lines(raw_lines)
        dodois_items = [
            {"rawMaterialId": "mat-a", "containerId": "con-a", "qty": 2.0, "totalWithVat": 10.00},
        ]
        result = match_lines(aggregated, dodois_items)
        assert len(result) == 1
        assert result[0]["description"] == "PRODUCT A"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/ask/Projects/erachun-dodois
python -m pytest tests/test_match_invoices.py -v 2>&1 | head -20
```

Expected: `ImportError` или `ModuleNotFoundError` — функции ещё не существуют.

- [ ] **Step 3: Add methods to DodoisClient**

В `app/core/dodois_client.py` добавить после `get_raw_materials`:

```python
    def get_all_supplies(self, dept_id: str, from_date: str, to_date: str) -> list:
        """Fetch all supplies for a department in date range (handles pagination)."""
        page, page_size = 1, 100
        all_supplies = []
        while True:
            r = self._session.get(
                f"{BASE}/incomingstock/departments/{dept_id}/supplies"
                f"?from={from_date}&to={to_date}"
                f"&pagination.current={page}&pagination.pageSize={page_size}",
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("items", data) if isinstance(data, dict) else data
            if not items:
                break
            all_supplies.extend(items)
            total = data.get("total", len(items)) if isinstance(data, dict) else len(items)
            if len(all_supplies) >= total or len(items) < page_size:
                break
            page += 1
        return all_supplies

    def get_supply_detail(self, supply_id: str) -> dict:
        """Fetch a single supply with its line items."""
        r = self._session.get(
            f"{BASE}/incomingstock/supplies/{supply_id}",
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 4: Commit DodoisClient changes**

```bash
git add app/core/dodois_client.py
git commit -m "feat: add get_all_supplies and get_supply_detail to DodoisClient"
```

---

## Task 2: Create scripts/match_invoices.py — pure functions

**Files:**
- Create: `scripts/match_invoices.py`
- Test: `tests/test_match_invoices.py`

- [ ] **Step 1: Create script with pure functions**

Create `scripts/match_invoices.py`:

```python
"""
Match eRačun invoices with Dodois supplies to auto-populate ProductMapping.

Usage:
    python scripts/match_invoices.py [--dry-run] [--from DATE] [--to DATE]

Options:
    --dry-run     Show what would be written without touching DB
    --from DATE   Start date (default: 2025-01-01)
    --to DATE     End date (default: today)
"""
import argparse
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure functions (tested in unit tests)
# ---------------------------------------------------------------------------

def dodois_to_eracun(inv_number: str) -> str:
    """
    Convert Dodois invoice number to eRačun format.

    Dodois: 6/0(011)0003/004488  →  eRačun: 4488/11/6003
    Formula: C/0(0BB)0YYY/00000A  →  A/BB/CYYY

    If already in eRačun format or unrecognised, return unchanged.
    """
    m = re.match(r"^(\d)/0\(0(\d+)\)0(\d{3})/0*(\d+)$", inv_number)
    if m:
        c, bb, yyy, a = m.groups()
        return f"{int(a)}/{int(bb)}/{c}{yyy}"
    return inv_number


def _line_value(line, *keys):
    """Get first non-None value from a dict or object for the given keys."""
    for key in keys:
        v = line.get(key) if isinstance(line, dict) else getattr(line, key, None)
        if v is not None:
            return v
    return None


def aggregate_ubl_lines(lines: list) -> list:
    """
    Merge duplicate UBL lines (same description, sum qty and line_total).
    METRO invoices often split the same product across multiple lines with qty=1.

    Accepts either UBLLineItem objects or dicts with keys: description/item_name,
    quantity, line_total.

    Returns list of dicts: {description, quantity, line_total}.
    """
    grouped: dict[str, dict] = {}
    for line in lines:
        desc = (
            _line_value(line, "item_name", "description") or ""
        ).strip()
        if not desc:
            continue
        qty = float(_line_value(line, "quantity") or 0)
        total = float(_line_value(line, "line_total") or 0)
        if desc in grouped:
            grouped[desc]["quantity"] += qty
            grouped[desc]["line_total"] = round(grouped[desc]["line_total"] + total, 4)
        else:
            grouped[desc] = {
                "description": desc,
                "quantity": qty,
                "line_total": round(total, 4),
            }
    return list(grouped.values())


def match_lines(ubl_lines: list, dodois_items: list,
                qty_tol: float = 0.01, price_tol: float = 0.02) -> list:
    """
    Match Dodois supply items to aggregated UBL invoice lines by qty + total price.

    ubl_lines: list of dicts {description, quantity, line_total}  (already aggregated)
    dodois_items: list of dicts {rawMaterialId, containerId, qty, totalWithVat}

    Returns list of match dicts:
        {description, rawMaterialId, containerId}

    Ambiguous matches (multiple UBL lines match one Dodois item) are skipped.
    """
    results = []
    for item in dodois_items:
        candidates = [
            line for line in ubl_lines
            if abs(line["quantity"] - item["qty"]) <= qty_tol
            and abs(line["line_total"] - item["totalWithVat"]) <= price_tol
        ]
        if len(candidates) == 1:
            results.append({
                "description": candidates[0]["description"],
                "rawMaterialId": item["rawMaterialId"],
                "containerId": item.get("containerId"),
            })
        elif len(candidates) > 1:
            logger.debug(
                "Ambiguous: rawMaterialId=%s qty=%s total=%s → %d candidates",
                item["rawMaterialId"], item["qty"], item["totalWithVat"], len(candidates),
            )
    return results
```

- [ ] **Step 2: Run tests — should pass now**

```bash
python -m pytest tests/test_match_invoices.py -v
```

Expected: все 12 тестов PASS.

- [ ] **Step 3: Commit**

```bash
git add scripts/match_invoices.py tests/test_match_invoices.py
git commit -m "feat: add match_invoices pure functions with tests"
```

---

## Task 3: Add DB write logic and main() to match_invoices.py

**Files:**
- Modify: `scripts/match_invoices.py`

- [ ] **Step 1: Append write_mappings() and main() to the script**

Добавить в конец `scripts/match_invoices.py`:

```python

# ---------------------------------------------------------------------------
# DB write
# ---------------------------------------------------------------------------

def write_mappings(session, invoice, matches: list, dry_run: bool) -> dict:
    """
    Upsert ProductMapping rows from matched lines.

    invoice: Invoice ORM object with .sender_oib, .sender_name
    Returns counts: {new, skipped_existing, skipped_no_catalog}
    """
    from app.db.models import (
        DodoisRawMaterialCatalog, ProductMapping, SupplierMapping,
        get_or_create_supplier_mapping,
    )

    supplier_mapping = session.query(SupplierMapping).filter_by(
        eracun_oib=invoice.sender_oib
    ).first()
    if not supplier_mapping:
        supplier_mapping = get_or_create_supplier_mapping(
            session, invoice.sender_oib, invoice.sender_name
        )

    counts = {"new": 0, "skipped_existing": 0, "skipped_no_catalog": 0}

    for match in matches:
        # Find catalog row by material + container
        raw_mat = session.query(DodoisRawMaterialCatalog).filter_by(
            dodois_material_id=match["rawMaterialId"],
            dodois_container_id=match["containerId"],
        ).first()
        if not raw_mat:
            # Fallback: any container for this material
            raw_mat = session.query(DodoisRawMaterialCatalog).filter_by(
                dodois_material_id=match["rawMaterialId"],
            ).first()
        if not raw_mat:
            logger.debug("No catalog row for rawMaterialId=%s", match["rawMaterialId"])
            counts["skipped_no_catalog"] += 1
            continue

        pm = session.query(ProductMapping).filter_by(
            supplier_mapping_id=supplier_mapping.id,
            eracun_description=match["description"],
        ).first()

        if pm and pm.dodois_raw_material_id is not None:
            counts["skipped_existing"] += 1
            continue

        if not dry_run:
            if pm:
                pm.dodois_raw_material_id = raw_mat.id
                pm.enabled = True
            else:
                session.add(ProductMapping(
                    supplier_mapping_id=supplier_mapping.id,
                    eracun_description=match["description"],
                    dodois_raw_material_id=raw_mat.id,
                    enabled=True,
                ))
        counts["new"] += 1

    if not dry_run:
        session.commit()
    return counts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Match eRačun invoices with Dodois supplies")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing DB")
    parser.add_argument("--from", dest="from_date", default="2025-01-01")
    parser.add_argument("--to", dest="to_date", default=str(date.today()))
    args = parser.parse_args()

    from app.core.config_loader import load_config, get_database_url
    from app.core.dodois_auth import DodoisSession
    from app.core.dodois_client import DodoisClient
    from app.core.ubl_parser import parse_invoice
    from app.db.models import Invoice, get_engine, get_session_factory

    cfg_path = os.environ.get("ERACUN_CONFIG", "config.yaml")
    local_cfg = "config.local.yaml"
    cfg = load_config(cfg_path, local_cfg if Path(local_cfg).exists() else None)

    xml_dir = Path(cfg.get("storage", {}).get("xml_dir", "/app/data/xmls"))
    db_url = get_database_url(cfg)
    session = get_session_factory(get_engine(db_url))()

    dodois_cfg = cfg.get("dodois", {})
    ds = DodoisSession(
        dodois_cfg["username"],
        dodois_cfg["password"],
        dodois_cfg.get("totp_secret", ""),
    )
    client = DodoisClient(ds)
    pizzerias = dodois_cfg.get("pizzerias", {})

    # Step 1: Fetch all Dodois supplies for all departments
    all_supplies_raw = []
    for piz_key, piz in pizzerias.items():
        dept_id = piz.get("department_id", "")
        if not dept_id:
            logger.warning("Skipping %s — no department_id", piz_key)
            continue
        logger.info("Fetching supplies for %s (%s)…", piz_key, dept_id)
        supplies = client.get_all_supplies(dept_id, args.from_date, args.to_date)
        logger.info("  Got %d supplies", len(supplies))
        all_supplies_raw.extend(supplies)

    # Deduplicate by (invoiceNumber, supplierId)
    seen: set = set()
    supplies_deduped = []
    for s in all_supplies_raw:
        key = (s.get("invoiceNumber"), s.get("supplierId"))
        if key not in seen:
            seen.add(key)
            supplies_deduped.append(s)
    logger.info("Unique supplies: %d (raw: %d)", len(supplies_deduped), len(all_supplies_raw))

    # Step 2: Match each supply to eRačun Invoice, then lines
    stats = dict(
        supplies=len(supplies_deduped),
        inv_matched=0, inv_not_found=0, inv_no_xml=0,
        line_matches=0,
        map_new=0, map_existing=0, map_no_catalog=0,
    )

    for supply_summary in supplies_deduped:
        dodois_inv_num = supply_summary.get("invoiceNumber", "")
        eracun_num = dodois_to_eracun(dodois_inv_num)

        invoice = (
            session.query(Invoice)
            .filter(Invoice.invoice_number.ilike(f"%{eracun_num}%"))
            .first()
        )
        if not invoice:
            logger.debug("Not found: %s → %s", dodois_inv_num, eracun_num)
            stats["inv_not_found"] += 1
            continue
        stats["inv_matched"] += 1

        if not invoice.xml_path:
            stats["inv_no_xml"] += 1
            continue
        xml_full = xml_dir / invoice.xml_path
        if not xml_full.exists():
            logger.warning("Missing XML: %s", xml_full)
            stats["inv_no_xml"] += 1
            continue

        try:
            ubl = parse_invoice(xml_full.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Parse failed %s: %s", invoice.invoice_number, exc)
            stats["inv_no_xml"] += 1
            continue

        ubl_lines_agg = aggregate_ubl_lines(ubl.lines)

        try:
            detail = client.get_supply_detail(supply_summary["id"])
        except Exception as exc:
            logger.warning("Detail fetch failed %s: %s", supply_summary.get("id"), exc)
            continue

        # Dodois API may use "supplyItems" or "items"
        dodois_items = detail.get("supplyItems") or detail.get("items") or []
        if not dodois_items:
            continue

        matches = match_lines(ubl_lines_agg, dodois_items)
        stats["line_matches"] += len(matches)

        counts = write_mappings(session, invoice, matches, dry_run=args.dry_run)
        stats["map_new"] += counts["new"]
        stats["map_existing"] += counts["skipped_existing"]
        stats["map_no_catalog"] += counts["skipped_no_catalog"]

    session.close()

    dry = " [DRY RUN]" if args.dry_run else ""
    print(f"\n=== match_invoices{dry} ===")
    print(f"Dodois supplies:         {stats['supplies']}")
    print(f"Invoices matched:        {stats['inv_matched']}")
    print(f"Invoices not found:      {stats['inv_not_found']}")
    print(f"Invoices without XML:    {stats['inv_no_xml']}")
    print(f"Line matches:            {stats['line_matches']}")
    print(f"ProductMappings new:     {stats['map_new']}")
    print(f"  already set (skipped): {stats['map_existing']}")
    print(f"  no catalog (skipped):  {stats['map_no_catalog']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify all tests still pass**

```bash
python -m pytest tests/test_match_invoices.py -v
```

Expected: все 12 тестов PASS.

- [ ] **Step 3: Commit**

```bash
git add scripts/match_invoices.py
git commit -m "feat: add write_mappings and main() to match_invoices"
```

---

## Task 4: Sync catalog + run on server

- [ ] **Step 1: Скопировать каталог на сервер**

```bash
sshpass -p 'Ask020713!' scp -o StrictHostKeyChecking=no \
  dodois-suppliers-clean.json \
  ask@80.233.248.54:/tmp/dodois-suppliers-clean.json
```

- [ ] **Step 2: Синхронизировать каталог в DB**

```bash
sshpass -p 'Ask020713!' ssh -o StrictHostKeyChecking=no ask@80.233.248.54 \
  "docker cp /tmp/dodois-suppliers-clean.json e-rachun-dodois:/tmp/ && \
   docker exec e-rachun-dodois python scripts/sync_dodois_catalog.py /tmp/dodois-suppliers-clean.json"
```

Ожидаем:
```
47 suppliers loaded
Materials: NNN added, 0 updated
```

- [ ] **Step 3: Rsync кода на сервер**

```bash
rsync -avz \
  --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.playwright-mcp' --exclude='.superpowers' --exclude='.claude' \
  --exclude='*.png' --exclude='dev.db' --exclude='*.json' \
  --exclude='docs/superpowers' \
  -e "sshpass -p 'Ask020713!' ssh -o StrictHostKeyChecking=no -o PasswordAuthentication=yes" \
  . ask@80.233.248.54:/opt/erachun-dodois/
```

- [ ] **Step 4: Dry-run на сервере**

```bash
sshpass -p 'Ask020713!' ssh -o StrictHostKeyChecking=no ask@80.233.248.54 \
  "docker exec e-rachun-dodois python scripts/match_invoices.py --dry-run --from 2025-01-01"
```

Проверить вывод: `Invoices matched` > 0, `Line matches` > 0, нет ERROR.

- [ ] **Step 5: Запустить реальный матчинг**

```bash
sshpass -p 'Ask020713!' ssh -o StrictHostKeyChecking=no ask@80.233.248.54 \
  "docker exec e-rachun-dodois python scripts/match_invoices.py --from 2025-01-01"
```

- [ ] **Step 6: Проверить результат в UI**

Открыть http://er.dodotool.com → Mappings → Products.  
Убедиться что маппинги появились.

- [ ] **Step 7: Финальный коммит**

```bash
git add scripts/match_invoices.py tests/test_match_invoices.py app/core/dodois_client.py
git commit -m "feat: auto product mapping via invoice cross-reference (all suppliers)"
```

---

## Notes

**Если `supplyItems` пустой** — проверить реальную структуру API:

```bash
sshpass -p 'Ask020713!' ssh -o StrictHostKeyChecking=no ask@80.233.248.54 \
  "docker exec e-rachun-dodois python -c \"
import json
from app.core.config_loader import load_config
from app.core.dodois_auth import DodoisSession
from app.core.dodois_client import DodoisClient
cfg = load_config('config.yaml', 'config.local.yaml')
d = cfg['dodois']
ds = DodoisSession(d['username'], d['password'], d.get('totp_secret',''))
client = DodoisClient(ds)
dept = list(cfg['dodois']['pizzerias'].values())[0]['department_id']
supplies = client.get_all_supplies(dept, '2026-01-01', '2026-04-08')
if supplies:
    detail = client.get_supply_detail(supplies[0]['id'])
    print(json.dumps(detail, indent=2)[:2000])
\""
```

**Если invoice_number не совпадает** — проверить формат в DB:

```bash
sshpass -p 'Ask020713!' ssh -o StrictHostKeyChecking=no ask@80.233.248.54 \
  "docker exec e-rachun-dodois python -c \"
from app.core.config_loader import load_config, get_database_url
from app.db.models import get_engine, get_session_factory, Invoice
cfg = load_config('config.yaml', 'config.local.yaml')
s = get_session_factory(get_engine(get_database_url(cfg)))()
for inv in s.query(Invoice).limit(5): print(repr(inv.invoice_number))
\""
```
