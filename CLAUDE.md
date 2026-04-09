# CLAUDE.md — Project Context for e-rachun - DodoIs

## What is this project

A web portal + Telegram bot for **Orange food business d.o.o.** (OIB: 52219073449) operating **Dodo Pizza** restaurants in Zagreb, Croatia. The system automates receiving supplier invoices from **moj-eRačun** (Croatian e-invoicing platform) and optionally uploading them into **Dodois** (Dodo IS — internal ERP system).

**Owner:** Andrey Koval (koval.dodo@gmail.com)

## Architecture

```
e-rachun-dodois/
├── docker-compose.yaml          # Caddy + Streamlit + PostgreSQL
├── Dockerfile                   # Python 3.12-slim
├── Caddyfile                    # Reverse proxy + auto-HTTPS (Let's Encrypt)
├── config.yaml                  # Settings, users, product mappings (committed)
├── config.local.yaml            # Secrets: eRačun/Dodois creds (NOT committed)
├── requirements.txt
├── .streamlit/config.toml       # Streamlit theme + fileWatcherType=none
├── .rsync-exclude               # Deploy exclusions (protect config.local.yaml)
│
├── app/
│   ├── core/
│   │   ├── eracun_client.py     # moj-eRačun API v2 client
│   │   ├── ubl_parser.py        # UBL 2.1 XML parser (Croatian HR-CIUS)
│   │   ├── invoice_sync.py      # Sync service: fetch → parse → save
│   │   ├── config_loader.py     # YAML config loader (merges config + local)
│   │   ├── dodois_auth.py       # Dodois OIDC session handler
│   │   ├── dodois_client.py     # Dodois REST API client (suppliers, supplies)
│   │   └── dodois_uploader.py   # Upload invoice → Dodois supply (with matching)
│   ├── db/
│   │   └── models.py            # SQLAlchemy models (Invoice, SyncLog, ProductMapping)
│   └── web/
│       └── app.py               # Streamlit UI (auth, invoice list, PDF viewer, upload)
│
├── scripts/
│   ├── gen_password.py          # bcrypt password hash generator
│   ├── fetch_dodois_catalog.py  # Fetch all raw materials from Dodois
│   ├── sync_dodois_catalog.py   # Sync catalog to local DB
│   ├── seed_metro_mappings.py   # Seed ProductMapping table from METRO history
│   ├── match_invoices.py        # Cross-reference invoices ↔ supplies for auto-mapping
│   └── debug_match.py           # Matching debug helper
│
├── tests/
│   ├── conftest.py
│   ├── test_dodois_uploader.py  # 16 tests for upload logic
│   └── test_match_invoices.py   # Tests for invoice matching
│
├── data/                        # Runtime data (gitignored)
│   ├── pdfs/                    # Extracted PDFs from UBL
│   └── xmls/                    # Raw invoice XMLs
│
├── docs/                        # Project docs
├── samples/                     # Local dev fixtures (gitignored)
│   ├── xmls/                    # Sample signed invoice XMLs
│   └── json/                    # Dodois API response dumps
└── screenshots/                 # Debug screenshots (gitignored)
```

## Deployment

**Server:** `er.dodotool.com` (user: `ask`)
**Path:** `/opt/erachun-dodois`
**Method:** Git-based

```bash
ssh ask@er.dodotool.com
cd /opt/erachun-dodois
git pull
docker compose down && docker compose up --build -d
```

HTTPS automatic via Caddy + Let's Encrypt. Local secrets live in `config.local.yaml` on the server (not in git).

## Two Stages

### Stage 1 (CURRENT — implemented):
- Web portal showing all incoming invoices with search/filter
- PDF preview extracted from UBL XML (embedded as base64)
- Manual XML upload (drag & drop)
- Auto-sync from eRačun API (needs credentials)
- Authentication (bcrypt, config.yaml users)

### Stage 2 (IMPLEMENTED as of 2026-04-09):
- Auto-upload to Dodois for enabled suppliers (METRO, Pivac, …) via `dodois_uploader.upload_invoice`
- Pizzeria selector (Zagreb-1 / Zagreb-2) in invoice detail view, partial upload supported (`skip_unmapped`)
- Telegram notification after successful upload (`app/core/telegram_notifier.py`) — sends PDF + caption to topic
- ProductMapping DB table (187 mappings across 9 suppliers) — CLAUDE.md hardcoded tables below are historical; real source of truth is the DB seeded via `scripts/seed_metro_mappings.py` and UI-driven mapping actions
- Still TODO: Telegram bot as alternative command interface, background auto-sync scheduler

---

## moj-eRačun API v2

**Documentation:** https://manual.moj-eracun.hr/documentation/api-specification/
**Contact:** integracije@moj-eracun.hr

### Authentication
Every request includes 5 fields in JSON body (NOT headers):
- `Username` — email/ID from MER (free for receivers)
- `Password` — login password
- `CompanyId` — OIB: `52219073449`
- `CompanyBu` — business unit (leave empty)
- `SoftwareId` — ERP identifier assigned by MER

**STATUS:** Credentials NOT YET obtained. Need to email integracije@moj-eracun.hr.

### Key Endpoints

| Method | URL | Purpose |
|--------|-----|---------|
| QueryInbox | `POST /apis/v2/queryInbox` | List incoming invoices |
| Receive | `POST /apis/v2/receive` | Download single invoice XML by ElectronicId |
| NotifyImport | `POST /apis/v2/notifyimport/{id}` | Mark as imported (prevents duplicates) |
| UpdateProcessStatus | `POST /apis/v2/UpdateDokumentProcessStatus` | Send accept/reject to supplier |

### QueryInbox filters:
- `From` / `To` — ISO datetime (YYYY-MM-DDThh:mm:ss)
- `StatusId` — 20=InValidation, 30=Sent, 40=Delivered, 50=Rejected, 60=Expired
- `ElectronicId` — filter single document

### QueryInbox response (array):
```json
{
  "ElectronicId": 394167,
  "DocumentNr": "20156256",
  "DocumentTypeId": 1,
  "DocumentTypeName": "Račun",
  "StatusId": 40,
  "StatusName": "Obrađen",
  "SenderBusinessNumber": "38016445738",
  "SenderBusinessName": "METRO Cash & Carry, d.o.o.",
  "Imported": false,
  "Sent": "2026-01-28T15:18:56",
  "Delivered": "2026-01-29T07:06:38"
}
```

### Process Status codes:
- 0 = APPROVED (Prihvaćen)
- 1 = REJECTED (Odbijen)
- 2 = PAYMENT_FULFILLED
- 3 = PAYMENT_PARTIALLY_FULFILLED

---

## UBL 2.1 XML Parsing (Croatian HR-CIUS)

### Namespaces
```
inv: urn:oasis:names:specification:ubl:schema:xsd:Invoice-2
cac: urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2
cbc: urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2
```

### Croatian OIB formats (must handle all):
- `HR38016445738` — HR prefix (in CompanyID)
- `9934:38016445738` — scheme prefix (in PartyIdentification/ID)
- `38016445738` — plain OIB (in EndpointID)

The parser tries multiple locations: `PartyLegalEntity/CompanyID` → `EndpointID` → `PartyIdentification/ID`, then strips prefixes.

### Embedded PDF
Invoices contain base64-encoded PDF in:
```xml
<cac:AdditionalDocumentReference>
  <cac:Attachment>
    <cbc:EmbeddedDocumentBinaryObject mimeCode="application/pdf">
      ...base64...
    </cbc:EmbeddedDocumentBinaryObject>
  </cac:Attachment>
</cac:AdditionalDocumentReference>
```

### Line aggregation
eRačun invoices (e.g., METRO) may have **duplicate lines** — same product appears multiple times with qty=1. These must be aggregated into a single line (sum quantities, recalculate totals) before uploading to Dodois.

---

## Dodois Integration (Stage 2)

### API Approach
**DESADV XML import was abandoned** — Croatian Dodois uses GUIDs, not numeric IDs. The working approach is direct REST API calls.

### Key Endpoints
Base: `https://officemanager.dodois.com`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/Accounting/v1/Suppliers?departmentId=...` | GET | List suppliers |
| `/Accounting/v1/incomingstock/departments/{dept}/suppliers/{supplier}/rawmaterials` | GET | Raw materials + containers |
| `/Accounting/v1/incomingstock/supplies` | POST | Create supply |
| `/Accounting/v1/incomingstock/supplies/{id}` | GET | Get existing supply |
| `/Accounting/v1/taxrates` | GET | Tax rates |

### Authentication
Currently via browser cookies (session-based). For automation, need to reverse-engineer auth or use a persistent session.

### Critical IDs

```yaml
# METRO supplier
supplier_id: "11eeeb8be458f06caf0d5b3908d3a4aa"
supplier_oib: "38016445738"

# Pivac supplier (meat — added 2026-04-09)
supplier_id: "11f10e7e4945bd6c8a79b5471dd03c96"

# Pizzerias
zagreb-1:
  department_id: "E67B8C27D336AE8311EDE29371DEF8F6"
  unit_id: "e67b8c27d336ae8611ede2943f258e31"
zagreb-2:
  department_id: "e67b8c27d336ae8311ede29371def8f6"  # SUSPECT — same UUID as zagreb-1 (case-only diff); verify in Office Manager
  unit_id: "11efed30...fb280"                          # different from zagreb-1, inventory separation is at unit level

# Tax rates
tax_25: "c2f84413b0f6bba911ee2c6a71f95c44"
tax_13: "e67b8c27d336ae8811ede297eb178d65"
tax_5:  "7ec6687213e7bc4e11ee5e0c2fe41370"
tax_0:  "11eef744519b7360879c21c0dc22e49c"
```

### Product Mapping (historical — authoritative source is `product_mappings` DB table)

As of 2026-04-09 there are ~187 mappings across 9 suppliers in the `product_mappings` table. The tables below are kept as examples of the expected shape. New mappings are added via the Streamlit UI or regex-based seed scripts, not by editing this file.

#### METRO examples

| Product | rawMaterialId | containerId | unit | cSize | Notes |
|---------|--------------|-------------|------|-------|-------|
| Vindi Sok 0.25L | 11f04c6c20e7689dab59d130df32c874 | **null** | 1 (pcs) | 1 | NO container! Use pcs |
| Jalapeno 450g | 11eef67e9f071a84b9bf035189a8c3b4 | 11ef21a9a6d6257646ae4a288c274c30 | 5 (g) | 450 | |
| Corn Flour 1kg | 11ef598b10a74749b2f2681d5eb6ed44 | 11ef598ba73759a5f13bdee407eca450 | 5 | 1000 | |
| White sauce 1L | 11ef63950a77c4a8940f1b449952238b | 11ef6395b629bb5a214723fe063ca790 | 5 | 1000 | |
| Sour cream 180g | 11f0e250ce6ee2c5832a9c090a6d8427 | 11f0e250be46b7cb367cecb2c00e5070 | 5 | 180 | |
| Cheesecake 1250g | 11f064be7ddf3c9aad83e7d48b8ff087 | 11f064bebc0d9500c9116f177a9feb30 | 1 | 10 | |
| Cheddar 1kg | 11ef1f371be880cb9bb51697e04b8e98 | 11ef20fdaa55899b7337ef819c841010 | 5 | 1000 | |
| Blue Cheese 500g | 11eeeb8cf15b1d408deca6d020856fe5 | 11ef2114843b372d94e5d8b36e9d2670 | 5 | 500 | |
| Plastic bag 2L | 11f0801343f5d99a88b57718f762fc99 | 11f080138232457cadcce61b3f41dba0 | 1 | 25 | |
| Parmegiano 500g | 11ef59532de07eaea59d5fa9fa497c45 | 11ef595389b27dc8999d655923361800 | 5 | 500 | |
| Black Olives 935g | 11f09e01e6cdf0628af93a405cd35683 | 11f09e018cdf7bb30ddfe358d5033b00 | 5 | 935 | |
| Napkins 500pcs | 11f003ea671ff4d2b15b238cb59cef6b | 11f003e8aa0583db93718fcc52e94280 | 1 | 500 | |
| Paper plate 50pcs | 11f064c02b3f45288220ae67f82dbd7c | 11f064c0962369a37ea1d8f424f0baa0 | 1 | 50 | |
| Plastic bag 100pcs | 11f0ad16c3e6a934ad48e59b9522af90 | 11f0ad16b819997cbcd56d3dbffe1170 | 1 | 100 | |
| Baking paper 8m | 11f04c6ce021468f916d5998122c3167 | 11f04c6ca01ac1708f541944dd2b0b50 | 8 (m) | 8 | |

#### Pivac examples (added 2026-04-09)

| Product | rawMaterialId | containerId | unit | cSize | Notes |
|---------|--------------|-------------|------|-------|-------|
| Kulen narezak 500g (471883) | 11f10e7e8f2ed5b383fca9b33b7f2121 | 11f10e7e94c7f591e161f6538d779a90 | 5 | 500 | |
| Dalmatinska panceta narezak 500g (471655) | 11f10e7f05928995a378cee57d2885bb | 11f10e7e94c7f591e161f653e8816ba0 | 5 | 500 | |
| Pizza Šunka Rezana (472022) | 11f10e7e7aeefa2f81c4140d847a832c | **null** | 5 (g) | — | **Weighed, no container** — quantity goes in grams, see Price Calculation §weight-no-container |

### Price Calculation Formula

**CRITICAL — the API validates prices strictly:**

```python
# unit=5 (grams): price per kilogram
pricePerUnit = round(totalPrice / (qty * containerSize / 1000), 2)

# unit=1 (pieces): price per individual piece
pricePerUnit = round(totalPrice / (qty * containerSize), 2)

# unit=8 (meters): price per meter
pricePerUnit = round(totalPrice / (qty * containerSize), 2)
```

**Exception: Vindi Sok** — uses `containerId: null` (pcs without container). Formula becomes simply `totalPrice / qty`.

**§weight-no-container (Pivac case)** — raw material with `materialType.unitOfMeasure=5` AND `containers: []` (e.g. Pizza Šunka Rezana):
```python
# quantity field in payload MUST BE IN GRAMS (integer-like), not kilograms.
# If XML delivers qty in KGM, multiply by 1000 before sending.
qty_grams = xml_qty_kg * 1000
pricePerUnitWithoutVat = round(totalPriceWithoutVat / (qty_grams / 1000), 2)  # i.e. price per kg
```
If you send the XML KGM value (e.g. 30.176) as-is, the server rounds it to 0.03 internally and bounces the request with `SUPPLY_ITEM_PRICE_PER_UNIT_WITHOUT_VAT_WRONG_CALCULATION`. Helper: `dodois_uploader._compute_supply_quantity`. Detect weighed-no-container materials via `GET .../rawmaterials` → `materialType.unitOfMeasure == 5 && containers == []`.

### Lessons Learned (Dodois)

1. **Supply POST requires `id` field** — generate with `uuid.uuid4().hex`
2. **API recalculates pricePerUnit** — it uses totalPrice / (qty × containerSize) internally and validates against what you send
3. **Container selection matters** — Vindi Sok must use "pcs" (no container), NOT "Package 24 pcs". Using the package container causes price to be divided by 24 twice.
4. **Duplicate check (NOT YET IMPLEMENTED — tech debt)** — CLAUDE.md requires it, but `upload_invoice` doesn't do it. In the 2026-04-09 Pivac hotfix session, client-side retries after server-side failures created 6+ duplicate supplies on Dodois for the same invoice. Fix: before POST, call `GET supplies?from=invoice_date-1&to=+1`, match by `invoiceNumber + supplierId`, return existing id if found and not `isRemoved`.
5. **Invoice number format** — eRačun uses "2315/11/6005", manual Dodois entry might format it differently as "6/0(011)0005/002315". Both are accepted by API.
6. **`vatValue` = actual VAT amount in €**, NOT percentage. Example: 25% VAT on 31.89€ → `vatValue: 7.97` (not 25)
7. **`commercialInvoiceNumber` and `commercialInvoiceDate` are required** — set equal to `invoiceNumber` / `invoiceDate`
8. **Supplies list endpoint** — `GET /Accounting/v1/incomingstock/departments/{dept}/supplies?from=YYYY-MM-DD&to=YYYY-MM-DD`
9. **Successful POST returns empty body** — `/incomingstock/supplies` answers 2xx with NO content on success. Naïve `response.json()` raises `JSONDecodeError` and the caller loses the supply_id **even though the supply was actually created** (→ duplicates on retry). Client MUST do `if not r.text.strip(): return {}` and the caller must fall back to the `id` it generated in the payload. Fixed in commit `0b27a89`.
10. **Error body formats vary & mislead** — on 400 you may get:
    - `{"Errors": {"SUPPLY_ITEM_TOTAL_PRICE_WITH_VAT_WRONG_CALCULATION": [{"ExpectedValue": "84.6", "ActualValue": "84.6", ...}]}}` — Expected/Actual can look **identical** because the server formats with low precision; the real check runs deeper (e.g. `pricePerUnitWithVat = pricePerUnitWithoutVat × (1 + taxRate)`) and the error name points to the wrong field.
    - `{"errors": {"unitId": ["Error converting value \"\" to type 'Dodo.Primitives.Uuid'."]}}` — .NET ModelState format for missing/empty required fields.
    - Always surface the raw body on failure — `dodois_client.create_supply` now raises `RuntimeError` with the full body (commit `184a775`), don't regress to `raise_for_status()`.
11. **Per-line TaxAmount may be missing** — Pivac's Croatian UBL puts `<cac:TaxTotal>/<cbc:TaxAmount>` ONLY at document level; `<cac:InvoiceLine>` carries just `<cac:ClassifiedTaxCategory>/<cbc:Percent>`. Uploader must fall back to `tax_amount = line_total × tax_percent / 100` when XML line-level VAT is 0, else payload goes out with `vatValue=0 + taxId=25%` and is rejected. Fixed in commit `782fbd5`.
12. **`config.local.yaml` empty-string override footgun** — `_deep_merge` in `app/core/config_loader.py` overwrites base keys with ANY override value, including `""`. Stale `zagreb-2.department_id: ''` in the server's `config.local.yaml` silently nuked the real value from `config.yaml` and caused `unitId` validation errors. When diagnosing config issues on the server, diff both files — do NOT trust `config.yaml` alone.

### Supply POST Payload Structure
```json
{
  "id": "<uuid.hex>",
  "supplierId": "11eeeb8be458f06caf0d5b3908d3a4aa",
  "unitId": "<pizzeria unit_id>",
  "invoiceNumber": "2315/11/6005",
  "commercialInvoiceNumber": "2315/11/6005",
  "invoiceDate": "2026-03-23",
  "commercialInvoiceDate": "2026-03-23",
  "receiptDateTime": "2026-03-23T12:00:00",
  "hasVat": true,
  "currencyCode": 978,
  "supplyItems": [
    {
      "quantity": 18,
      "rawMaterialId": "11eef67e9f071a84b9bf035189a8c3b4",
      "rawMaterialContainerId": "11ef21a9a6d6257646ae4a288c274c30",
      "taxId": "c2f84413b0f6bba911ee2c6a71f95c44",
      "vatValue": 11.74,
      "totalPriceWithVat": 58.72,
      "totalPriceWithoutVat": 46.98,
      "pricePerUnitWithVat": 7.25,
      "pricePerUnitWithoutVat": 5.8
    }
  ]
}
```

---

## TODO

### Done (historical reference):
- [x] eRačun API credentials obtained (prod: 433737 / demo: 13272)
- [x] Deploy to VPS with Docker — running on `er.dodotool.com` behind Caddy + HTTPS
- [x] `dodois_client.py` / `dodois_uploader.py` implemented with 16+ tests
- [x] Dodois upload UI with pizzeria selector + partial upload
- [x] Telegram notification after successful upload
- [x] Support for 9 suppliers beyond METRO (Pivac added 2026-04-09)

### Tech debt (from 2026-04-09 Pivac session — see `project_dodois_tech_debt.md`):
- [ ] **Duplicate-check in `upload_invoice` before POST** — see Lessons Learned §4. 6+ duplicate supplies were created for a single Pivac invoice during the retry storm. Use `get_all_supplies(dept, issue_date-1, issue_date+1)` + match on `invoiceNumber + supplierId + not isRemoved`.
- [ ] **Verify Zagreb-2 `department_id`** — current value is just the Zagreb-1 UUID in lowercase, which is case-insensitively identical. Unit_id differs, so inventory may still be separated, but confirm in Office Manager UI or with Dodois integration team.
- [ ] **Decide `_deep_merge` behaviour for empty override values** — empty strings in `config.local.yaml` silently nuke valid base values. Either patch merge logic to ignore `""` / `None`, or enforce a lint step on deploy.

### Still open from Stage 2 scope:
- [ ] Telegram **bot** as alternative command interface (`app/bot.py`) — currently only outbound notifications exist
- [ ] Background scheduler for auto-sync (APScheduler)
- [ ] Fuzzy auto-matching of new products (current matching is EAN → exact name → regex seed)
- [ ] Invoice approval workflow
- [ ] Financial reporting / analytics dashboard

---

## Development Notes

### Running locally (without Docker):
```bash
pip install -r requirements.txt
# Set up PostgreSQL or use SQLite for dev:
# In config.yaml, change database.url to: sqlite:///./dev.db
streamlit run app/web/app.py
```

### Key design decisions:
- **Streamlit over Flask/React** — fastest path to working UI for non-technical users
- **PostgreSQL over SQLite** — multi-user concurrent access, production-ready
- **Config.yaml over .env** — single file for all settings including product mappings
- **Direct Dodois API over DESADV XML** — DESADV import failed because Croatian Dodois uses GUIDs not numeric IDs
- **Embedded PDF extraction** — UBL XML contains base64-encoded PDF, no need for separate PDF download
- **No container for Vindi Sok** — must use `rawMaterialContainerId: null` to avoid double-division in price calc
