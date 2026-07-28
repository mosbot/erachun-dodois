# PlanFact Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Post Wolt and Glovo invoices to PlanFact as outcome operations directly from stored eRačun data, replacing the separate `pdf2planfact` service.

**Architecture:** A cron step (`scripts/post_to_planfact.py`) runs after the eRačun sync, selects invoices that have no PlanFact operation yet, validates them, and posts a two-item outcome operation (commission + input VAT) against the Wolt/Glovo account and the Zagreb-1/Zagreb-2 project. Anything that fails stays unposted and is retried on the next run.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, requests/httpx, pytest, PostgreSQL, Streamlit.

## Global Constraints

- Scope is Wolt (OIB `25531986377`) and Glovo (OIB `48879371584`) only.
- Wolt Drive — invoice-number middle segment `2553198637741` — is never posted.
- Credit notes (`document_type_id == 381`) are never posted.
- An invoice is marked posted **only** when an operation id exists.
- Pizzeria detection must never read the buyer party (`cac:AccountingCustomerParty`).
- Pizzeria keys everywhere are the display names `Zagreb-1` / `Zagreb-2`, matching `invoices.dodois_pizzeria`.
- Money comes from stored totals; never reconstruct by subtraction.
- All user-visible UI strings are English (project rule).
- `Base.metadata.create_all` never alters existing tables — new columns need explicit DDL.
- Spec: `docs/superpowers/specs/2026-07-28-planfact-integration-design.md`.

---

### Task 1: Config-driven pizzeria detection

Rewrites `_detect_pizzeria` so it reads only delivery-related fields, takes its patterns from config, and refuses to guess when two pizzerias match. Today it scans a few fixed paths, knows only `TRATIN`/`MAKSIMIR`, and returns `Zagreb-1` for invoices whose *buyer* address is Kranjčevićeva.

**Files:**
- Modify: `app/core/ubl_parser.py` (`_detect_pizzeria`, `parse_ubl_xml`)
- Modify: `app/core/invoice_sync.py` (pass patterns from config)
- Modify: `config.yaml` (new `pizzeria_detection` block)
- Test: `tests/test_ubl_parser.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `ubl_parser.DEFAULT_PIZZERIA_PATTERNS: dict[str, list[str]]`
  - `ubl_parser.parse_ubl_xml(xml_content, pizzeria_patterns: Optional[dict] = None) -> UBLInvoice`
  - `ubl_parser._detect_pizzeria(root, patterns: dict) -> Optional[str]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ubl_parser.py`:

```python
# ── Pizzeria detection ───────────────────────────────────────────────────────

def _wolt_invoice(delivery_location_id="", delivery_street="",
                  buyer_street="Trgovacka Ulica - Via Merceria 147"):
    loc = ""
    if delivery_location_id or delivery_street:
        loc = f"""    <cac:DeliveryLocation>
      <cbc:ID>{delivery_location_id}</cbc:ID>
      <cac:Address><cbc:StreetName>{delivery_street}</cbc:StreetName></cac:Address>
    </cac:DeliveryLocation>
"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice {INV_NS}>
  <cbc:ID>90247-2553198637711-2026</cbc:ID>
  <cbc:IssueDate>2026-07-10</cbc:IssueDate>
  <cac:AccountingCustomerParty><cac:Party>
    <cac:PostalAddress><cbc:StreetName>{buyer_street}</cbc:StreetName></cac:PostalAddress>
  </cac:Party></cac:AccountingCustomerParty>
  <cac:Delivery>
    <cbc:ActualDeliveryDate>2026-07-10</cbc:ActualDeliveryDate>
{loc}  </cac:Delivery>
  <cac:LegalMonetaryTotal>
    <cbc:TaxExclusiveAmount>100.00</cbc:TaxExclusiveAmount>
  </cac:LegalMonetaryTotal>
</Invoice>"""


def _glovo_invoice(item_name):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice {INV_NS}>
  <cbc:ID>2653343/G1/2234278</cbc:ID>
  <cbc:IssueDate>2026-07-22</cbc:IssueDate>
  <cac:LegalMonetaryTotal>
    <cbc:TaxExclusiveAmount>209.78</cbc:TaxExclusiveAmount>
  </cac:LegalMonetaryTotal>
  <cac:InvoiceLine>
    <cbc:ID>1</cbc:ID>
    <cbc:InvoicedQuantity unitCode="H87">1</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount>209.78</cbc:LineExtensionAmount>
    <cac:Item><cbc:Name>{item_name}</cbc:Name></cac:Item>
  </cac:InvoiceLine>
</Invoice>"""


def test_pizzeria_from_wolt_delivery_location_id():
    ubl = parse_ubl_xml(_wolt_invoice(
        delivery_location_id="4ujG1qM.65e990340c64206ab0881c8c"))
    assert ubl.delivery_pizzeria == "Zagreb-1"


def test_pizzeria_from_wolt_delivery_street():
    ubl = parse_ubl_xml(_wolt_invoice(delivery_street="Maksimirska cesta 120"))
    assert ubl.delivery_pizzeria == "Zagreb-2"


def test_buyer_address_is_never_used_for_detection():
    """Real case: buyer moved to Maksimirska while delivery stayed Kranjčevićeva."""
    ubl = parse_ubl_xml(_wolt_invoice(
        delivery_street="Kranjčevićeva ulica 1",
        buyer_street="Maksimirska cesta 120"))
    assert ubl.delivery_pizzeria == "Zagreb-1"


def test_no_delivery_location_yields_none():
    ubl = parse_ubl_xml(_wolt_invoice())
    assert ubl.delivery_pizzeria is None


def test_pizzeria_from_glovo_p_code():
    ubl = parse_ubl_xml(_glovo_invoice(
        "Glovo provizija P705447 račun broj: 47262-1-5-2026"))
    assert ubl.delivery_pizzeria == "Zagreb-1"


def test_pizzeria_from_glovo_p_code_zagreb2():
    ubl = parse_ubl_xml(_glovo_invoice(
        "Glovo provizija P825763 račun broj: 47284-1-5-2026"))
    assert ubl.delivery_pizzeria == "Zagreb-2"


def test_ambiguous_document_yields_none():
    """Two pizzerias matched — refuse rather than guess."""
    ubl = parse_ubl_xml(_wolt_invoice(
        delivery_location_id="65e990340c64206ab0881c8c",
        delivery_street="Maksimirska cesta 120"))
    assert ubl.delivery_pizzeria is None


def test_custom_patterns_override_defaults():
    ubl = parse_ubl_xml(
        _wolt_invoice(delivery_street="Nova Ulica 5"),
        pizzeria_patterns={"Zagreb-3": ["NOVA ULICA"]})
    assert ubl.delivery_pizzeria == "Zagreb-3"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_ubl_parser.py -k pizzeria -v`
Expected: FAIL — `test_buyer_address_is_never_used_for_detection` and the Glovo/LocationID cases return `None`; `test_custom_patterns_override_defaults` fails with `TypeError: parse_ubl_xml() got an unexpected keyword argument`.

- [ ] **Step 3: Replace `_detect_pizzeria` in `app/core/ubl_parser.py`**

Delete the existing `_detect_pizzeria` body and put this in its place:

```python
# Patterns are matched case-insensitively against delivery-related text only.
# Keys are the display names stored in ``invoices.dodois_pizzeria``.
DEFAULT_PIZZERIA_PATTERNS = {
    "Zagreb-1": [
        "TRATIN",                          # food suppliers' spelling
        "KRANJČEVIĆEVA", "KRANJCEVICEVA",  # Wolt / Glovo delivery street
        "TREŠNJEVKA", "TRESNJEVKA",
        "P705447",                         # Glovo venue code
        "65E990340C64206AB0881C8C",        # Wolt venue id
    ],
    "Zagreb-2": [
        "MAKSIMIR",
        "P825763",
        "67E560DAFF93AB813B57E0C2",
    ],
}


def _delivery_hints(root) -> str:
    """Collect text that may name the delivery point.

    Deliberately excludes cac:AccountingCustomerParty: the company's registered
    address is not where the goods went, and reading it made Wolt invoices
    delivered to Kranjčevićeva look like Maksimirska ones.
    """
    hints = []

    delivery = root.find(".//cac:Delivery", NS)
    if delivery is not None:
        for el in delivery.iter():
            if el.text and el.text.strip():
                hints.append(el.text)

    for xpath in (".//cac:OrderReference/cbc:ID",
                  "cbc:Note",
                  ".//cac:InvoiceLine/cac:Item/cbc:Name",
                  ".//cac:CreditNoteLine/cac:Item/cbc:Name"):
        for el in root.findall(xpath, NS):
            if el.text and el.text.strip():
                hints.append(el.text)

    return " ".join(hints).upper()


def _detect_pizzeria(root, patterns: dict) -> Optional[str]:
    """Detect the delivery pizzeria, or None when it cannot be told apart.

    Returns None when two different pizzerias match — a document that names
    both is a data problem, and booking it to a guessed one is worse than
    leaving it for a human.
    """
    combined = _delivery_hints(root)
    matched = {
        name for name, pats in patterns.items()
        if any(p.upper() in combined for p in pats)
    }
    if len(matched) == 1:
        return matched.pop()
    if len(matched) > 1:
        logger.warning(
            "Ambiguous pizzeria detection: %s matched on one document",
            ", ".join(sorted(matched)),
        )
    return None
```

- [ ] **Step 4: Thread the patterns through `parse_ubl_xml`**

In `app/core/ubl_parser.py` change the signature and the call site:

```python
def parse_ubl_xml(xml_content: Union[str, bytes],
                  pizzeria_patterns: Optional[dict] = None) -> UBLInvoice:
```

and replace the existing detection call with:

```python
    inv.delivery_pizzeria = _detect_pizzeria(
        root, pizzeria_patterns or DEFAULT_PIZZERIA_PATTERNS)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_ubl_parser.py -v`
Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 6: Add the config block**

In `config.yaml`, at the top level (not nested under `dodois`):

```yaml
# Pizzeria detection patterns, matched against delivery fields, notes and line
# item names. Keys must match invoices.dodois_pizzeria exactly.
pizzeria_detection:
  Zagreb-1: ["TRATIN", "KRANJČEVIĆEVA", "KRANJCEVICEVA", "TREŠNJEVKA",
             "TRESNJEVKA", "P705447", "65E990340C64206AB0881C8C"]
  Zagreb-2: ["MAKSIMIR", "P825763", "67E560DAFF93AB813B57E0C2"]
```

- [ ] **Step 7: Use the config in the sync service**

In `app/core/invoice_sync.py`, both `parse_ubl_xml(...)` call sites must pass the
patterns. Add to `InvoiceSyncService.__init__`:

```python
        pizzeria_patterns: Optional[dict] = None,
```
store it as `self.pizzeria_patterns = pizzeria_patterns`, and change both calls to:

```python
                ubl = parse_ubl_xml(xml_content, self.pizzeria_patterns)
```

Then in `scripts/sync_eracun.py` and `app/web/app.py`, where `InvoiceSyncService(...)`
is constructed, pass `pizzeria_patterns=cfg.get("pizzeria_detection")`.

- [ ] **Step 8: Run the whole suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS, 93 + 8 = 101 tests.

- [ ] **Step 9: Commit**

```bash
git add app/core/ubl_parser.py app/core/invoice_sync.py app/web/app.py \
        scripts/sync_eracun.py config.yaml tests/test_ubl_parser.py
git commit -m "fix: detect the pizzeria from delivery fields, never the buyer address"
```

---

### Task 2: PlanFact columns and configuration

**Files:**
- Modify: `app/db/models.py` (four columns on `Invoice`)
- Modify: `config.yaml` (`planfact` block)
- Test: `tests/test_planfact_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Invoice.planfact_operation_id`, `Invoice.planfact_external_id`, `Invoice.planfact_posted_at`, `Invoice.planfact_error`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_planfact_model.py`:

```python
from datetime import datetime

from app.db.models import Invoice


def test_invoice_has_planfact_columns(session):
    inv = Invoice(
        document_nr="X-1", sender_oib="25531986377", sender_name="Wolt Zagreb d.o.o.",
        invoice_number="X-1", processing_status="parsed",
        planfact_operation_id="283549770",
        planfact_external_id="87588-2553198637711-2026",
        planfact_posted_at=datetime(2026, 7, 6, 8, 0, 0),
        planfact_error=None,
    )
    session.add(inv)
    session.commit()

    stored = session.query(Invoice).filter_by(document_nr="X-1").one()
    assert stored.planfact_operation_id == "283549770"
    assert stored.planfact_external_id == "87588-2553198637711-2026"
    assert stored.planfact_posted_at.year == 2026
    assert stored.planfact_error is None


def test_planfact_fields_default_to_none(session):
    inv = Invoice(document_nr="X-2", sender_oib="1", sender_name="s",
                  invoice_number="X-2", processing_status="parsed")
    session.add(inv)
    session.commit()
    stored = session.query(Invoice).filter_by(document_nr="X-2").one()
    assert stored.planfact_operation_id is None
    assert stored.planfact_error is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_planfact_model.py -v`
Expected: FAIL — `TypeError: 'planfact_operation_id' is an invalid keyword argument for Invoice`.

- [ ] **Step 3: Add the columns**

In `app/db/models.py`, after the Dodois block:

```python
    # PlanFact integration
    # planfact_operation_id is the queue marker: NULL means "not posted yet".
    planfact_operation_id = Column(String(64), nullable=True, index=True)
    # Match key against PlanFact. Wolt: our invoice number. Glovo: the inner
    # number from the line item ("račun broj: 47284-1-5-2026").
    planfact_external_id = Column(String(100), nullable=True, index=True)
    planfact_posted_at = Column(DateTime, nullable=True)
    planfact_error = Column(Text, nullable=True)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python3 -m pytest tests/test_planfact_model.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Add the config block**

In `config.yaml`:

```yaml
planfact:
  enabled: true
  base_url: "https://api.planfact.io/api/v1"
  api_key: ""            # real value lives in config.local.yaml
  accounts:
    wolt: 666927
    glovo: 666928
  projects:              # keyed by invoices.dodois_pizzeria
    Zagreb-1: 1172400
    Zagreb-2: 1198217
  categories:
    wolt_commission: 8563181
    glovo_commission: 8563431
    vat: 9485374
  providers:
    wolt:
      oib: "25531986377"
      exclude_series: ["2553198637741"]   # Wolt Drive — not booked
    glovo:
      oib: "48879371584"
      exclude_series: []
```

- [ ] **Step 6: Commit**

```bash
git add app/db/models.py config.yaml tests/test_planfact_model.py
git commit -m "feat: add PlanFact tracking columns and configuration"
```

---

### Task 3: PlanFact HTTP client

**Files:**
- Create: `app/core/planfact_client.py`
- Test: `tests/test_planfact_client.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `PlanfactError(RuntimeError)`
  - `PlanfactClient(api_key: str, base_url: str = "https://api.planfact.io/api/v1", timeout: float = 15.0, max_retries: int = 3, retry_delay: float = 5.0)`
  - `PlanfactClient.create_outcome(payload: dict) -> dict`
  - `PlanfactClient.list_operations(account_id: int, date_from: str, date_to: str) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_planfact_client.py`:

```python
import pytest
from unittest.mock import MagicMock

from app.core.planfact_client import PlanfactClient, PlanfactError


def _client(session):
    c = PlanfactClient(api_key="k", retry_delay=0)
    c.session = session
    return c


def _response(status=200, json_data=None, text="{}"):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.json.return_value = json_data if json_data is not None else {}
    return r


def test_create_outcome_returns_data():
    s = MagicMock()
    s.post.return_value = _response(json_data={"isSuccess": True,
                                               "data": {"operationId": 42}})
    assert _client(s).create_outcome({"value": 1})["data"]["operationId"] == 42


def test_create_outcome_tolerates_empty_body():
    """A 2xx with no body must not raise — Dodois taught us this the hard way."""
    s = MagicMock()
    s.post.return_value = _response(text="   ")
    assert _client(s).create_outcome({"value": 1}) == {}


def test_business_error_raises_without_retry():
    s = MagicMock()
    s.post.return_value = _response(
        json_data={"isSuccess": False, "errorMessage": "bad project",
                   "errorCode": "E1"})
    with pytest.raises(PlanfactError, match="bad project"):
        _client(s).create_outcome({"value": 1})
    assert s.post.call_count == 1


def test_client_error_is_not_retried():
    s = MagicMock()
    s.post.return_value = _response(status=400, text="bad request")
    with pytest.raises(PlanfactError):
        _client(s).create_outcome({"value": 1})
    assert s.post.call_count == 1


def test_server_error_is_retried_then_raises():
    s = MagicMock()
    s.post.return_value = _response(status=503, text="upstream down")
    with pytest.raises(PlanfactError):
        _client(s).create_outcome({"value": 1})
    assert s.post.call_count == 3


def test_list_operations_paginates():
    s = MagicMock()
    page1 = _response(json_data={"data": {"items": [{"operationId": i}
                                                    for i in range(100)]}})
    page2 = _response(json_data={"data": {"items": [{"operationId": 100}]}})
    s.post.side_effect = [page1, page2]
    ops = _client(s).list_operations(666927, "2026-07-01", "2026-07-31")
    assert len(ops) == 101
    assert s.post.call_count == 2
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_planfact_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.planfact_client'`.

- [ ] **Step 3: Write the client**

Create `app/core/planfact_client.py`:

```python
"""
PlanFact API client.

Base URL: https://api.planfact.io/api/v1
Auth:     X-ApiKey header
Docs:     https://apidoc.planfact.io/

Only two calls are needed: create an outcome operation, and list operations so
we can tell whether one already exists. PlanFact does NOT enforce externalId
uniqueness — five duplicated operations already exist from the previous
service — so listing is a required part of posting, not an optimisation.
"""
import logging
import time

import requests

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


class PlanfactError(RuntimeError):
    """PlanFact rejected the request, or kept failing after retries."""


class PlanfactClient:
    def __init__(self, api_key: str,
                 base_url: str = "https://api.planfact.io/api/v1",
                 timeout: float = 15.0,
                 max_retries: int = 3,
                 retry_delay: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.session = requests.Session()
        self.session.headers.update({
            "X-ApiKey": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def create_outcome(self, payload: dict) -> dict:
        url = f"{self.base_url}/operations/outcome"
        last_error = "no attempt made"

        for attempt in range(1, self.max_retries + 1):
            try:
                r = self.session.post(url, json=payload, timeout=self.timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("PlanFact network error (attempt %d/%d): %s",
                               attempt, self.max_retries, exc)
            else:
                if r.status_code in (200, 201):
                    # A successful POST may answer with no body at all.
                    if not (r.text or "").strip():
                        return {}
                    data = r.json()
                    if data.get("isSuccess") is False:
                        raise PlanfactError(
                            f"{data.get('errorMessage', 'unknown error')} "
                            f"(code={data.get('errorCode')})")
                    return data
                if r.status_code < 500:
                    raise PlanfactError(
                        f"HTTP {r.status_code}: {(r.text or '')[:300]}")
                last_error = f"HTTP {r.status_code}: {(r.text or '')[:300]}"
                logger.warning("PlanFact server error (attempt %d/%d): %s",
                               attempt, self.max_retries, last_error)

            if attempt < self.max_retries:
                time.sleep(self.retry_delay * attempt)

        raise PlanfactError(
            f"giving up after {self.max_retries} attempts: {last_error}")

    def list_operations(self, account_id: int,
                        date_from: str, date_to: str) -> list:
        """Return every operation on an account within an inclusive date range."""
        url = f"{self.base_url}/operations/list"
        out, offset = [], 0
        while True:
            r = self.session.post(
                url,
                params={"paging.offset": offset, "paging.limit": PAGE_SIZE},
                json={"accountId": [account_id],
                      "operationDateStart": date_from,
                      "operationDateEnd": date_to},
                timeout=self.timeout,
            )
            if r.status_code != 200:
                raise PlanfactError(
                    f"list_operations HTTP {r.status_code}: {(r.text or '')[:300]}")
            items = ((r.json().get("data") or {}).get("items") or [])
            out.extend(items)
            if len(items) < PAGE_SIZE:
                return out
            offset += PAGE_SIZE
```

- [ ] **Step 4: Run them to verify they pass**

Run: `python3 -m pytest tests/test_planfact_client.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add app/core/planfact_client.py tests/test_planfact_client.py
git commit -m "feat: add PlanFact API client"
```

---

### Task 4: Candidate selection, external id and validation

**Files:**
- Create: `app/core/planfact_poster.py`
- Test: `tests/test_planfact_poster.py`

**Interfaces:**
- Consumes: `Invoice.planfact_*` columns (Task 2).
- Produces:
  - `resolve_provider(cfg: dict, invoice) -> Optional[str]` → `"wolt"` / `"glovo"` / `None`
  - `extract_external_id(provider: str, invoice, xml_text: str) -> Optional[str]`
  - `validate_invoice(cfg: dict, invoice, provider: Optional[str], external_id: Optional[str]) -> list[str]`
  - `select_candidates(session, cfg: dict, limit: Optional[int] = None) -> list`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_planfact_poster.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_planfact_poster.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.planfact_poster'`.

- [ ] **Step 3: Write the module**

Create `app/core/planfact_poster.py`:

```python
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
```

- [ ] **Step 4: Run them to verify they pass**

Run: `python3 -m pytest tests/test_planfact_poster.py -v`
Expected: PASS (14 tests).

- [ ] **Step 5: Commit**

```bash
git add app/core/planfact_poster.py tests/test_planfact_poster.py
git commit -m "feat: select and validate Wolt/Glovo invoices for PlanFact"
```

---

### Task 5: Payload construction and deduplicated posting

**Files:**
- Modify: `app/core/planfact_poster.py`
- Test: `tests/test_planfact_poster.py`

**Interfaces:**
- Consumes: `PlanfactClient` (Task 3); `resolve_provider`, `extract_external_id`, `validate_invoice` (Task 4).
- Produces:
  - `build_payload(cfg: dict, invoice, provider: str, external_id: str) -> dict`
  - `find_existing_operation(client, cfg: dict, invoice, provider: str, external_id: str) -> Optional[str]`
  - `post_invoice(client, cfg: dict, invoice, xml_text: str, dry_run: bool = False) -> tuple[Optional[str], Optional[str]]` returning `(operation_id, error)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_planfact_poster.py`:

```python
from unittest.mock import MagicMock

from app.core.planfact_poster import (
    build_payload, find_existing_operation, post_invoice,
)


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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_planfact_poster.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_payload'`.

- [ ] **Step 3: Add the implementation**

First add these two imports to the **top** of `app/core/planfact_poster.py`,
alongside the existing ones — not at the bottom with the appended code:

```python
from datetime import timedelta

from app.core.planfact_client import PlanfactError
```

Then append the rest to the end of the module:

```python
COMMENT_MARKER = "#erachun"
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
    """
    provider = resolve_provider(cfg, invoice)
    external_id = extract_external_id(provider, invoice, xml_text) if provider else None

    issues = validate_invoice(cfg, invoice, provider, external_id)
    if issues:
        return None, "; ".join(issues)

    try:
        existing = find_existing_operation(client, cfg, invoice, provider, external_id)
        if existing:
            logger.info("Invoice %s already in PlanFact as operation %s",
                        invoice.invoice_number, existing)
            return existing, None

        if dry_run:
            return None, None

        payload = build_payload(cfg, invoice, provider, external_id)
        response = client.create_outcome(payload)
        op_id = ((response or {}).get("data") or {}).get("operationId")
        if op_id:
            return str(op_id), None

        # Empty or unexpected body: the operation may still have been created.
        # Look it up rather than risk posting it twice.
        found = find_existing_operation(client, cfg, invoice, provider, external_id)
        if found:
            return found, None
        return None, "PlanFact returned no operation id and none was found afterwards"

    except PlanfactError as exc:
        return None, str(exc)
```

- [ ] **Step 4: Run them to verify they pass**

Run: `python3 -m pytest tests/test_planfact_poster.py -v`
Expected: PASS (24 tests).

- [ ] **Step 5: Commit**

```bash
git add app/core/planfact_poster.py tests/test_planfact_poster.py
git commit -m "feat: build and post deduplicated PlanFact operations"
```

---

### Task 6: Reconcile script for invoices already posted by pdf2planfact

Marks the ~126 invoices `pdf2planfact` posted so the first live run does not
post them a second time. Also adds the four columns to an existing database,
which `create_all` never does.

**Files:**
- Create: `scripts/reconcile_planfact.py`

**Interfaces:**
- Consumes: `PlanfactClient` (Task 3), `resolve_provider` / `extract_external_id` (Task 4).
- Produces: nothing other tasks import.

- [ ] **Step 1: Write the script**

Create `scripts/reconcile_planfact.py`:

```python
"""
Mark invoices that pdf2planfact already posted to PlanFact.

Run this once before the first live posting run. Without it the poster would
create a second copy of every invoice booked between 2025-12-31 and 2026-07-05,
because PlanFact does not reject duplicate externalIds.

Also adds the planfact_* columns when they are missing —
``Base.metadata.create_all`` only creates missing tables, never missing columns.

Usage:
    python scripts/reconcile_planfact.py            # dry-run
    python scripts/reconcile_planfact.py --apply    # write changes
"""
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from sqlalchemy import inspect, text

from app.core.config_loader import load_config, get_database_url, get_storage_config
from app.core.planfact_client import PlanfactClient
from app.core.planfact_poster import resolve_provider, extract_external_id
from app.db.models import get_engine, get_session_factory, Invoice

COLUMNS = {
    "planfact_operation_id": "VARCHAR(64)",
    "planfact_external_id": "VARCHAR(100)",
    "planfact_posted_at": "TIMESTAMP",
    "planfact_error": "TEXT",
}


def ensure_columns(engine, apply_changes: bool) -> bool:
    existing = {c["name"] for c in inspect(engine).get_columns("invoices")}
    missing = {n: t for n, t in COLUMNS.items() if n not in existing}
    if not missing:
        print("Columns: all present")
        return True
    if not apply_changes:
        print(f"Columns MISSING: {', '.join(sorted(missing))} "
              f"(would be added with --apply)")
        return False
    with engine.begin() as conn:
        for name, coltype in missing.items():
            conn.execute(text(f"ALTER TABLE invoices ADD COLUMN {name} {coltype}"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_invoices_planfact_operation_id "
                          "ON invoices (planfact_operation_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_invoices_planfact_external_id "
                          "ON invoices (planfact_external_id)"))
    print(f"Columns ADDED: {', '.join(sorted(missing))}")
    return True


def main(apply_changes: bool) -> int:
    cfg = load_config()
    pf = cfg.get("planfact", {}) or {}
    if not pf.get("api_key"):
        print("PlanFact not configured (planfact.api_key missing)")
        return 1

    engine = get_engine(get_database_url(cfg))
    if not ensure_columns(engine, apply_changes):
        print("\nRun again with --apply to add the columns first.")
        return 1

    session = get_session_factory(engine)()
    xml_dir = Path(get_storage_config(cfg).get("xml_dir", "/app/data/xmls"))
    client = PlanfactClient(pf["api_key"], base_url=pf.get(
        "base_url", "https://api.planfact.io/api/v1"))

    # externalId -> operationId, across both accounts
    posted = {}
    for provider, account_id in (pf.get("accounts") or {}).items():
        ops = client.list_operations(account_id, "2025-01-01", "2030-12-31")
        for op in ops:
            ext = op.get("externalId")
            if ext:
                posted.setdefault((provider, str(ext)), str(op.get("operationId")))
        print(f"{provider}: {len(ops)} operations in PlanFact")

    oibs = [str((c or {}).get("oib", "")) for c in (pf.get("providers") or {}).values()]
    invoices = (session.query(Invoice)
                .filter(Invoice.processing_status != "deleted")
                .filter(Invoice.sender_oib.in_([o for o in oibs if o]))
                .order_by(Invoice.id).all())

    matched, already, unmatched = [], 0, []
    for inv in invoices:
        provider = resolve_provider(cfg, inv)
        if not provider:
            continue
        xml_text = ""
        if inv.xml_path and (xml_dir / inv.xml_path).exists():
            xml_text = (xml_dir / inv.xml_path).read_text(
                encoding="utf-8", errors="replace")
        ext = extract_external_id(provider, inv, xml_text)
        if inv.planfact_operation_id:
            already += 1
            continue
        op_id = posted.get((provider, ext)) if ext else None
        if op_id:
            matched.append((inv, ext, op_id))
            if apply_changes:
                inv.planfact_operation_id = op_id
                inv.planfact_external_id = ext
                inv.planfact_posted_at = datetime.utcnow()
        else:
            unmatched.append((inv, ext))
            if apply_changes and ext:
                inv.planfact_external_id = ext

    print()
    print(f"Invoices scanned          : {len(invoices)}")
    print(f"Already marked            : {already}")
    print(f"Matched to PlanFact       : {len(matched)}")
    print(f"Not in PlanFact (to post) : {len(unmatched)}")
    print()
    if unmatched:
        print(f"{'invoice':<28} {'external id':<22} {'vat date':<11} {'gross':>9}  pizzeria")
        for inv, ext in unmatched:
            print(f"{(inv.invoice_number or '')[:28]:<28} {(ext or '—')[:22]:<22} "
                  f"{inv.vat_date.strftime('%Y-%m-%d') if inv.vat_date else '—':<11} "
                  f"{(inv.total_with_vat or 0):>9.2f}  "
                  f"{inv.dodois_pizzeria or '— UNKNOWN'}")
        print()

    if apply_changes:
        session.commit()
        print("Changes committed.")
    else:
        print("Dry-run — nothing written. Re-run with --apply to commit.")
    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
```

- [ ] **Step 2: Check it imports and its help path works**

Run: `python3 -c "import ast; ast.parse(open('scripts/reconcile_planfact.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Run the whole suite to confirm nothing regressed**

Run: `python3 -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/reconcile_planfact.py
git commit -m "feat: reconcile invoices already posted to PlanFact by pdf2planfact"
```

---

### Task 7: Posting CLI with failure notifications

**Files:**
- Create: `scripts/post_to_planfact.py`
- Modify: `app/core/planfact_poster.py` (add `should_notify`)
- Modify: `app/core/telegram_notifier.py` (add `send_alert`)
- Modify: `config.yaml` (`telegram.alerts_chat_id`)
- Test: `tests/test_planfact_poster.py`, `tests/test_telegram_notifier.py`

**Interfaces:**
- Consumes: everything from Tasks 3–5.
- Produces:
  - `planfact_poster.should_notify(previous_error: Optional[str], new_error: str) -> bool`
  - `telegram_notifier.send_alert(bot_token: str, chat_id, text: str, topic_id=None, timeout: float = 15.0) -> tuple[bool, Optional[str]]`

**Why a new notifier function:** `send_invoice_notification` formats a
Dodois-specific caption ending in "Supply auto-created in Dodois — please
verify". Reusing it for a PlanFact failure would send a message that says the
opposite of what happened.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_planfact_poster.py`:

```python
from app.core.planfact_poster import should_notify


def test_notify_on_first_failure():
    assert should_notify(None, "boom") is True


def test_do_not_notify_on_repeated_identical_failure():
    """The job runs every 30 minutes; the same error must not spam Telegram."""
    assert should_notify("boom", "boom") is False


def test_notify_when_the_failure_changes():
    assert should_notify("boom", "different boom") is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_planfact_poster.py -k notify -v`
Expected: FAIL — `ImportError: cannot import name 'should_notify'`.

- [ ] **Step 3: Add the helper**

Append to `app/core/planfact_poster.py`:

```python
def should_notify(previous_error: Optional[str], new_error: str) -> bool:
    """Notify only when a failure is new or has changed.

    The poster runs every 30 minutes and a failing invoice stays in the queue,
    so notifying every time would send the same message 48 times a day.
    """
    return (previous_error or "") != (new_error or "")
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python3 -m pytest tests/test_planfact_poster.py -k notify -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Write the failing test for the alert sender**

Append to `tests/test_telegram_notifier.py`:

```python
from unittest.mock import patch

from app.core.telegram_notifier import send_alert


def test_send_alert_posts_text_to_the_topic():
    with patch("app.core.telegram_notifier.requests.post") as post:
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"ok": True}
        ok, err = send_alert("tok", 123, "PlanFact failed", topic_id=7)

    assert (ok, err) == (True, None)
    url, kwargs = post.call_args[0][0], post.call_args[1]
    assert url.endswith("/bottok/sendMessage")
    assert kwargs["data"]["chat_id"] == "123"
    assert kwargs["data"]["text"] == "PlanFact failed"
    assert kwargs["data"]["message_thread_id"] == "7"


def test_send_alert_reports_api_error():
    with patch("app.core.telegram_notifier.requests.post") as post:
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"ok": False,
                                               "description": "chat not found"}
        ok, err = send_alert("tok", 123, "x")

    assert ok is False
    assert "chat not found" in err


def test_send_alert_requires_a_chat():
    ok, err = send_alert("tok", None, "x")
    assert ok is False
```

Run: `python3 -m pytest tests/test_telegram_notifier.py -k alert -v`
Expected: FAIL — `ImportError: cannot import name 'send_alert'`.

- [ ] **Step 6: Add the alert sender**

Append to `app/core/telegram_notifier.py`:

```python
def send_alert(bot_token: str, chat_id, text: str,
               topic_id: Optional[int] = None,
               timeout: float = 15.0) -> tuple[bool, Optional[str]]:
    """Send a plain text alert. Best-effort, like the invoice notifier.

    Separate from send_invoice_notification because that one always signs off
    with a Dodois success caption, which is the wrong thing to say about a
    failure.
    """
    if not bot_token:
        return False, "bot_token is empty"
    if not chat_id:
        return False, "chat_id is empty"

    try:
        data = {"chat_id": str(chat_id), "text": text}
        if topic_id is not None:
            data["message_thread_id"] = str(topic_id)
        resp = requests.post(
            f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage",
            data=data, timeout=timeout)
        if resp.status_code != 200:
            return False, f"Telegram API {resp.status_code}: {resp.text[:200]}"
        body = resp.json()
        if not body.get("ok"):
            return False, f"Telegram API error: {body.get('description', 'unknown')}"
        return True, None
    except requests.RequestException as e:
        logger.warning("Telegram alert failed: %s", e)
        return False, str(e)
```

Run: `python3 -m pytest tests/test_telegram_notifier.py -v`
Expected: PASS.

- [ ] **Step 7: Add the alert chat to config**

In `config.yaml`, add a `telegram` block (the bot token itself stays in
`config.local.yaml`):

```yaml
telegram:
  bot_token: ""          # real value lives in config.local.yaml
  # Integration failures go to the admin's own chat — one recipient, not the
  # per-pizzeria topics used for upload notifications. Restaurant staff cannot
  # act on a PlanFact error. Without this set, failures are only visible in the
  # log and in the invoice detail panel.
  alerts_chat_id: ""
  alerts_topic_id: ""
```

- [ ] **Step 8: Write the CLI**

Create `scripts/post_to_planfact.py`:

```python
"""
CLI entrypoint for posting Wolt/Glovo invoices to PlanFact.

Called by the host cron job via ``sync_invoices.sh``, right after
``sync_eracun.py``. Anything that fails stays unposted and is retried on the
next run — that is the whole point of running it on a schedule.

Exit codes:
    0 — run completed (any number of invoices posted, including 0)
    1 — configuration missing (PlanFact API key not set)
    2 — runtime error

Usage:
    python scripts/post_to_planfact.py
    python scripts/post_to_planfact.py --dry-run
    python scripts/post_to_planfact.py --invoice 918
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from app.core.config_loader import load_config, get_database_url, get_storage_config
from app.core.planfact_client import PlanfactClient
from app.core.planfact_poster import post_invoice, select_candidates, should_notify
from app.core.telegram_notifier import send_alert
from app.db.models import get_engine, get_session_factory, Invoice

logger = logging.getLogger("post_to_planfact")


def _notify_failure(cfg: dict, invoice: Invoice, error: str) -> None:
    """Report a new failure to Telegram. Never raises — this is best-effort."""
    tg = cfg.get("telegram", {}) or {}
    bot_token = (tg.get("bot_token") or "").strip()
    chat_id = tg.get("alerts_chat_id")
    if not bot_token or not chat_id:
        logger.warning("Telegram alerts not configured — failure not notified")
        return

    date = invoice.vat_date or invoice.issue_date
    text = (
        f"❌ PlanFact posting failed\n"
        f"{invoice.sender_name}\n"
        f"Invoice {invoice.invoice_number}"
        f"{' · ' + date.strftime('%d.%m.%Y') if date else ''}\n"
        f"{(invoice.total_with_vat or 0.0):,.2f} EUR"
        f"{' · ' + invoice.dodois_pizzeria if invoice.dodois_pizzeria else ''}\n"
        f"\n{error}"
    )
    ok, err = send_alert(bot_token, chat_id, text,
                         topic_id=tg.get("alerts_topic_id") or None)
    if not ok:
        logger.warning("Telegram alert failed: %s", err)


def main() -> int:
    parser = argparse.ArgumentParser(description="Post invoices to PlanFact")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be posted, send nothing")
    parser.add_argument("--invoice", type=int, default=None,
                        help="Process a single invoice by database id")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config()
    pf = cfg.get("planfact", {}) or {}
    if not pf.get("enabled", False):
        logger.info("PlanFact integration disabled (planfact.enabled=false)")
        return 0
    if not pf.get("api_key"):
        logger.error("PlanFact not configured (planfact.api_key missing)")
        return 1

    engine = get_engine(get_database_url(cfg))
    session = get_session_factory(engine)()
    xml_dir = Path(get_storage_config(cfg).get("xml_dir", "/app/data/xmls"))

    try:
        if args.invoice:
            candidates = [session.query(Invoice).get(args.invoice)]
            candidates = [c for c in candidates if c]
        else:
            candidates = select_candidates(session, cfg)

        if not candidates:
            logger.info("Nothing to post")
            return 0

        logger.info("%d invoice(s) to consider%s",
                    len(candidates), " (dry run)" if args.dry_run else "")

        client = PlanfactClient(
            pf["api_key"],
            base_url=pf.get("base_url", "https://api.planfact.io/api/v1"))

        posted = skipped = failed = 0
        for inv in candidates:
            xml_text = ""
            if inv.xml_path and (xml_dir / inv.xml_path).exists():
                xml_text = (xml_dir / inv.xml_path).read_text(
                    encoding="utf-8", errors="replace")

            op_id, error = post_invoice(client, cfg, inv, xml_text,
                                        dry_run=args.dry_run)

            if op_id:
                if not args.dry_run:
                    inv.planfact_operation_id = op_id
                    inv.planfact_posted_at = datetime.utcnow()
                    inv.planfact_error = None
                    session.commit()
                posted += 1
                logger.info("POSTED  %s -> operation %s (%.2f EUR, %s)",
                            inv.invoice_number, op_id, inv.total_with_vat or 0.0,
                            inv.dodois_pizzeria)
            elif error:
                notify = should_notify(inv.planfact_error, error)
                if not args.dry_run:
                    inv.planfact_error = error
                    session.commit()
                    if notify:
                        _notify_failure(cfg, inv, error)
                failed += 1
                logger.error("FAILED  %s: %s", inv.invoice_number, error)
            else:
                skipped += 1
                logger.info("WOULD POST  %s (%.2f EUR, %s)",
                            inv.invoice_number, inv.total_with_vat or 0.0,
                            inv.dodois_pizzeria)

        logger.info("Done: posted=%d failed=%d would-post=%d",
                    posted, failed, skipped)
        return 0

    except Exception as exc:
        logger.error("Run failed: %s", exc, exc_info=True)
        return 2
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 9: Verify it parses and the suite is green**

Run: `python3 -c "import ast; ast.parse(open('scripts/post_to_planfact.py').read()); print('ok')" && python3 -m pytest tests/ -q`
Expected: `ok`, then all tests PASS.

- [ ] **Step 10: Commit**

```bash
git add scripts/post_to_planfact.py app/core/planfact_poster.py \
        app/core/telegram_notifier.py config.yaml \
        tests/test_planfact_poster.py tests/test_telegram_notifier.py
git commit -m "feat: add PlanFact posting CLI with admin failure alerts"
```

---

### Task 8: PlanFact status in the invoice detail panel

**Files:**
- Modify: `app/web/app.py` (invoice detail panel)

**Interfaces:**
- Consumes: `Invoice.planfact_*` (Task 2).
- Produces: nothing.

- [ ] **Step 1: Add the status block**

In `app/web/app.py`, inside `render_invoice_detail`, immediately before the call
to `render_dodois_upload_block(...)`, insert:

```python
    _render_planfact_status(inv)
```

and add this function next to `render_dodois_upload_block`:

```python
def _render_planfact_status(inv: Invoice):
    """Show whether the invoice reached PlanFact. Read-only — posting is automatic."""
    cfg = get_config()
    pf = cfg.get("planfact", {}) or {}
    if not pf.get("enabled"):
        return
    provider_oibs = {str((c or {}).get("oib", ""))
                     for c in (pf.get("providers") or {}).values()}
    if (inv.sender_oib or "") not in provider_oibs:
        return

    st.divider()
    st.markdown("**PlanFact**")
    if inv.planfact_operation_id:
        posted = (inv.planfact_posted_at.strftime("%d.%m.%Y %H:%M")
                  if inv.planfact_posted_at else "—")
        st.markdown(
            f"✅ Posted · operation `{inv.planfact_operation_id}` · {posted}")
    elif inv.planfact_error:
        st.markdown(f"❌ Not posted — {inv.planfact_error}")
    else:
        st.markdown("⏳ Queued — will be posted on the next sync")
```

- [ ] **Step 2: Verify the app still parses and the suite is green**

Run: `python3 -c "import ast; ast.parse(open('app/web/app.py').read()); print('ok')" && python3 -m pytest tests/ -q`
Expected: `ok`, then all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add app/web/app.py
git commit -m "feat: show PlanFact posting status in the invoice detail panel"
```

---

### Task 9: Wire into cron and document

**Files:**
- Modify: `sync_invoices.sh`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `scripts/post_to_planfact.py` (Task 7).
- Produces: nothing.

- [ ] **Step 1: Add the posting step to the cron wrapper**

In `sync_invoices.sh`, replace the body of `run()` with:

```bash
run() {
  echo "[$(ts)] sync start"
  # -T disables TTY allocation (required under cron).
  ${COMPOSE} exec -T web python scripts/sync_eracun.py
  local rc=$?
  echo "[$(ts)] sync done rc=${rc}"

  # Post Wolt/Glovo invoices to PlanFact. Runs under the same flock, so it
  # never overlaps itself and always sees freshly synced invoices. A failure
  # here must not mask a sync failure, so the sync's rc is what we return.
  echo "[$(ts)] planfact start"
  ${COMPOSE} exec -T web python scripts/post_to_planfact.py
  echo "[$(ts)] planfact done rc=$?"

  return ${rc}
}
```

- [ ] **Step 2: Document the integration in CLAUDE.md**

Add a section after the Dodois integration section:

```markdown
## PlanFact Integration

Wolt and Glovo invoices are posted to PlanFact as outcome operations by
`scripts/post_to_planfact.py`, which `sync_invoices.sh` runs right after the
eRačun sync. This replaced the standalone `pdf2planfact` service, which parsed
the same invoices out of PDFs in Gmail and silently dropped any message whose
processing failed.

Each operation carries two items — commission (`total_without_vat`) and input
VAT (`total_vat`) — against the Wolt/Glovo account and the Zagreb-1/Zagreb-2
project. `operationDate` is `vat_date`, which reproduces the billing-period end
the old service computed from the PDF.

**Not posted:** Wolt Drive (invoice-number series `2553198637741` — a separate
contract with no venue), credit notes, and anything whose pizzeria cannot be
determined.

**Deduplication is ours.** PlanFact does not enforce `externalId` uniqueness —
five duplicated operations from the old service prove it. Before each POST the
poster lists operations within ±3 days of `vat_date` and looks for the external
id. Wolt's external id is our invoice number; Glovo's is the inner number from
the line item (`Glovo provizija P705447 račun broj: 47284-1-5-2026`), because
eRačun and PlanFact number Glovo invoices differently.

Run `scripts/reconcile_planfact.py --apply` once on a database that predates the
integration: it adds the `planfact_*` columns and marks what the old service
already posted.

**Failures go to the admin only** (`telegram.alerts_chat_id`), not to the
per-pizzeria topics used for Dodois upload notifications — restaurant staff
cannot act on a PlanFact error. A notification is sent only when the failure is
new or its text changed, because the job retries every 30 minutes.
```

- [ ] **Step 3: Commit**

```bash
git add sync_invoices.sh CLAUDE.md
git commit -m "feat: run PlanFact posting from the sync cron job"
```

---

## Rollout (after all tasks are merged and deployed)

1. Put the API key into `config.local.yaml` on the server under `planfact.api_key`,
   and the admin's chat id under `telegram.alerts_chat_id`. Check `config.yaml`
   and `config.local.yaml` together — an empty string in the local file silently
   overrides the base value (see CLAUDE.md).
2. `docker compose exec -T web python scripts/remap_pizzerias.py` — dry-run,
   review, then `--apply`. Expect Zagreb-1 to appear on Wolt invoices for the
   first time.
3. `docker compose exec -T web python scripts/reconcile_planfact.py` — dry-run,
   then `--apply`. Expect ~126 invoices matched and 14 listed as still to post.
4. `docker compose exec -T web python scripts/post_to_planfact.py --dry-run` —
   expect exactly those 14 invoices, 8 923.53 EUR gross.
5. Run it live, then verify in PlanFact that 14 new operations exist and no
   externalId appears twice.
6. Deploy the updated `sync_invoices.sh` and stop running `pdf2planfact`.
