# CLAUDE.md — Project Context for eRačun Portal

## What is this project

A web portal + Telegram bot for **Orange food business d.o.o.** (OIB: 52219073449) operating **Dodo Pizza** restaurants in Zagreb, Croatia. The system automates receiving supplier invoices from **moj-eRačun** (Croatian e-invoicing platform) and optionally uploading them into **Dodois** (Dodo IS — internal ERP system).

**Owner:** Andrey Koval (koval.dodo@gmail.com)

## Architecture

```
eracun-portal/
├── docker-compose.yaml          # PostgreSQL + Streamlit app
├── Dockerfile                   # Python 3.12-slim
├── config.yaml                  # All settings, users, product mappings
├── requirements.txt
├── .streamlit/config.toml       # Streamlit theme (orange)
├── scripts/gen_password.py      # bcrypt password hash generator
├── app/
│   ├── core/
│   │   ├── eracun_client.py     # moj-eRačun API v2 client
│   │   ├── ubl_parser.py        # UBL 2.1 XML parser (Croatian HR-CIUS)
│   │   ├── invoice_sync.py      # Sync service: fetch → parse → save
│   │   └── config_loader.py     # YAML config loader
│   ├── db/
│   │   └── models.py            # SQLAlchemy models (Invoice, SyncLog)
│   └── web/
│       └── app.py               # Streamlit UI (auth, invoice list, PDF viewer)
```

## Two Stages

### Stage 1 (CURRENT — implemented):
- Web portal showing all incoming invoices with search/filter
- PDF preview extracted from UBL XML (embedded as base64)
- Manual XML upload (drag & drop)
- Auto-sync from eRačun API (needs credentials)
- Authentication (bcrypt, config.yaml users)

### Stage 2 (TODO):
- For selected suppliers (configured in config.yaml), auto-upload invoices to Dodois
- Telegram bot as alternative interface
- User selects pizzeria (Zagreb-1 or Zagreb-2) for each invoice

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

# Pizzerias
zagreb-1:
  department_id: "E67B8C27D336AE8311EDE29371DEF8F6"
  unit_id: "e67b8c27d336ae8611ede2943f258e31"
zagreb-2:
  department_id: ""   # TODO: obtain
  unit_id: ""         # TODO: obtain

# Tax rates
tax_25: "c2f84413b0f6bba911ee2c6a71f95c44"
tax_13: "e67b8c27d336ae8811ede297eb178d65"
tax_5:  "7ec6687213e7bc4e11ee5e0c2fe41370"
tax_0:  "11eef744519b7360879c21c0dc22e49c"
```

### Product Mapping (METRO → Dodois)

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

### Lessons Learned (Dodois)

1. **Supply POST requires `id` field** — generate with `uuid.uuid4().hex`
2. **API recalculates pricePerUnit** — it uses totalPrice / (qty × containerSize) internally and validates against what you send
3. **Container selection matters** — Vindi Sok must use "pcs" (no container), NOT "Package 24 pcs". Using the package container causes price to be divided by 24 twice.
4. **Duplicate check** — Before creating a supply, check existing supplies by invoice number to avoid duplicates
5. **Invoice number format** — eRačun uses "2315/11/6005", manual Dodois entry might format it differently as "6/0(011)0005/002315"

### Supply POST Payload Structure
```json
{
  "id": "<uuid.hex>",
  "supplierId": "11eeeb8be458f06caf0d5b3908d3a4aa",
  "unitId": "<pizzeria unit_id>",
  "invoiceNumber": "2315/11/6005",
  "invoiceDate": "2026-03-23",
  "receiptDateTime": "2026-03-23T12:00:00",
  "hasVat": true,
  "currencyCode": 978,
  "supplyItems": [
    {
      "quantity": 18,
      "rawMaterialId": "11eef67e9f071a84b9bf035189a8c3b4",
      "rawMaterialContainerId": "11ef21a9a6d6257646ae4a288c274c30",
      "taxId": "c2f84413b0f6bba911ee2c6a71f95c44",
      "vatValue": 25,
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

### Immediate:
- [ ] Get eRačun API credentials (email integracije@moj-eracun.hr)
- [ ] Get Zagreb-2 department_id and unit_id from Dodois
- [ ] Deploy to VPS with Docker
- [ ] Test eRačun sync with real credentials
- [ ] Generate proper bcrypt password hashes for users

### Stage 2:
- [ ] Build `dodois_client.py` — REST API client for creating supplies
- [ ] Build `product_matcher.py` — match invoice lines to Dodois raw materials (EAN first, then name substring)
- [ ] Build `price_calculator.py` — calculate prices per unit using container formula
- [ ] Add Dodois upload button in Streamlit UI with pizzeria selector (Zagreb-1/Zagreb-2)
- [ ] Build Telegram bot (`app/bot.py`)
- [ ] Add background scheduler for auto-sync (APScheduler)
- [ ] Add nginx reverse proxy with HTTPS (Let's Encrypt)

### Future:
- [ ] Support more suppliers beyond METRO
- [ ] Auto-match new products (fuzzy name matching)
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
