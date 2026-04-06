# External Integrations

**Analysis Date:** 2026-04-06

## APIs & External Services

**moj-eRačun (Croatian e-invoicing platform):**
- Service: Receive electronic invoices from suppliers
  - SDK/Client: Custom `app/core/eracun_client.py` (REST API v2 client using httpx)
  - Auth: JSON body credentials (Username, Password, CompanyId, SoftwareId, CompanyBu)
  - Env config: `eracun.base_url`, `eracun.username`, `eracun.password`, `eracun.software_id`, `eracun.company_id` in config.yaml
  - Status: **Credentials NOT YET obtained** - awaiting email reply from integracije@moj-eracun.hr

**Dodois (Dodo IS - internal ERP system):**
- Service: Upload invoices as supplies, retrieve suppliers and product catalogs
  - SDK/Client: Custom `app/core/dodois_client.py` (planned for Stage 2) - REST API client using httpx
  - Auth: Bearer token extracted from browser DevTools (session-based)
  - Env config: `dodois.base_url`, `dodois.token` in config.yaml
  - Endpoints:
    - `GET /Accounting/v1/Suppliers?departmentId=...` - List suppliers
    - `GET /Accounting/v1/incomingstock/departments/{dept}/suppliers/{supplier}/rawmaterials` - Product catalog
    - `POST /Accounting/v1/incomingstock/supplies` - Create supply (invoice upload)
    - `GET /Accounting/v1/incomingstock/supplies/{id}` - Retrieve existing supply
    - `GET /Accounting/v1/taxrates` - Fetch tax rate IDs
  - Base: `https://officemanager.dodois.com`

## Data Storage

**Databases:**

**PostgreSQL 16:**
- Primary data store for invoices, sync logs, supplier/product mappings
- Connection: `postgresql://eracun:eracun_secret@postgres:5432/e_rachun_dodois` (docker-compose)
- Client: SQLAlchemy 2.0.36 ORM (`app/db/models.py`)
- Alternative for local dev: SQLite (sqlite:///./dev.db)

**File Storage:**

**Local filesystem only:**
- Invoice PDFs: `/app/data/pdfs/` (extracted from UBL XML embedded PDF)
- Invoice XMLs: `/app/data/xmls/` (raw XML from eRačun API)
- Mounted as Docker volume: `invoice_data:/app/data`

**Caching:**
- None detected. Supplier and product catalogs cached in PostgreSQL tables (`dodois_supplier_catalog`, `dodois_raw_material_catalog`)

## Authentication & Identity

**Auth Provider:** Custom (local)
- Implementation: bcrypt password hashing
- Storage: config.yaml users section with bcrypt hashes
- Flow: Streamlit-authenticator middleware validates username/password against hashed values
- Password generation: `python scripts/gen_password.py` (wrapper around bcrypt.gensalt)

**User Roles:**
- `admin` - Full access (Andrey Koval)
- `viewer` - (Planned) Read-only access

## Monitoring & Observability

**Error Tracking:**
- None detected (no Sentry, Rollbar, etc.)
- Errors logged to standard logging module

**Logs:**
- Python logging module (console)
- Streamlit server logs via `streamlit run`
- SyncLog table tracks sync operations: `started_at`, `finished_at`, `status`, `error_message`

## CI/CD & Deployment

**Hosting:**
- Docker container (production: VPS or Kubernetes)
- Streamlit cloud (experimental, not currently used)

**CI Pipeline:**
- None detected (no GitHub Actions, GitLab CI, etc.)

**Container Orchestration:**
- docker-compose (local and simple deployments)
- Kubernetes (future)

## Environment Configuration

**Required env vars:**
- `ERACUN_CONFIG` - Path to config.yaml (default: /app/config.yaml)

**Critical config values (in config.yaml):**
- `eracun.username`, `eracun.password`, `eracun.software_id` - moj-eRačun credentials (pending)
- `eracun.company_id` - OIB: 52219073449 (Orange food business d.o.o.)
- `dodois.token` - Bearer token (extracted from browser)
- `dodois.pizzerias[].department_id` and `unit_id` - Pizzeria identifiers in Dodois
- Database credentials in docker-compose.yaml

**Secrets location:**
- config.yaml contains plaintext passwords and tokens (NOT .gitignore'd - **security risk**)
- Docker compose env vars (docker-compose.yaml) for PostgreSQL auth
- NO .env file in use currently

## Webhooks & Callbacks

**Incoming:**
- None detected (Streamlit is pull-based, no webhook receiver)

**Outgoing:**
- Dodois integration planned to POST supplies via REST API (not webhooks)

## Product Mapping & Catalogs

**METRO Supplier (OIB: 38016445738):**
- Dodois Supplier ID: `11eeeb8be458f06caf0d5b3908d3a4aa`
- 15 products pre-mapped in `app/db/models.py` seed data, including:
  - Vindi Sok 0.25L (unit: pcs, NO container - special case)
  - Jalapeno 450g (unit: grams with container)
  - Corn Flour 1kg, White Sauce 1L, Sour Cream 180g, Cheesecake, Cheddar, etc.
  - Napkins, Paper plates, Plastic bags (unit: pcs)
  - Baking paper (unit: meters)

**Other Suppliers (configured but disabled):**
- 22 additional suppliers pre-configured with Dodois IDs in config.yaml
- Awaiting eRačun OIB confirmation and product mapping

## Tax Rates (Dodois)

**Cached IDs:**
- 25%: `c2f84413b0f6bba911ee2c6a71f95c44`
- 13%: `e67b8c27d336ae8811ede297eb178d65`
- 5%: `7ec6687213e7bc4e11ee5e0c2fe41370`
- 0%: `11eef744519b7360879c21c0dc22e49c`

## Pizzeria Configuration (Dodois)

**Zagreb-1 (enabled):**
- Department ID: `E67B8C27D336AE8311EDE29371DEF8F6`
- Unit ID: `e67b8c27d336ae8611ede2943f258e31`

**Zagreb-2 (pending):**
- Department ID: (TODO - obtain from Dodois admin)
- Unit ID: (TODO - obtain from Dodois admin)

---

*Integration audit: 2026-04-06*
