# Architecture

**Analysis Date:** 2026-04-06

## Pattern Overview

**Overall:** Layered monolith with clear separation of concerns: external API client, domain logic, persistence, and web UI.

**Key Characteristics:**
- Three-tier architecture: web (Streamlit UI) → core services → database models
- External API abstraction layer for moj-eRačun integration
- Configuration-driven behavior with YAML overlays (config.yaml + config.local.yaml)
- Stateful database for invoice tracking and mapping metadata
- Direct ORM usage (SQLAlchemy) for database operations

## Layers

**Web Layer (Presentation):**
- Purpose: Streamlit-based UI for invoice management, supplier mapping, PDF preview
- Location: `app/web/app.py`
- Contains: Page renderers, authentication, form handling, Dodois catalog syncing
- Depends on: Core services, database models, config loader
- Used by: End users via browser

**Core Layer (Business Logic):**
- Purpose: External integrations and invoice processing
- Location: `app/core/`
- Contains:
  - `eracun_client.py` — moj-eRačun API v2 client (query inbox, receive invoices, notify)
  - `invoice_sync.py` — synchronization service (fetch → parse → save)
  - `ubl_parser.py` — UBL 2.1 XML parsing for Croatian HR-CIUS format
  - `config_loader.py` — YAML config management with overlay support
- Depends on: External libraries (httpx, lxml), database models
- Used by: Web layer, sync scheduler (Stage 2)

**Persistence Layer (Data):**
- Purpose: ORM models and database initialization
- Location: `app/db/models.py`
- Contains: SQLAlchemy models (Invoice, SyncLog, DodoisSupplierCatalog, SupplierMapping, DodoisRawMaterialCatalog, ProductMapping)
- Depends on: SQLAlchemy
- Used by: Web layer, core services

## Data Flow

**Invoice Import (Manual or Auto-sync):**

1. **Manual XML Upload:** User drops XML file in Streamlit UI (`render_upload_page()`)
2. **Auto-sync from eRačun:** Admin triggers sync button → `sync_invoices()` → `InvoiceSyncService`
3. **API Call:** `EracunClient.query_inbox()` fetches list of invoices with `QueryInbox` endpoint
4. **Download:** `EracunClient.receive()` downloads single invoice XML by `electronic_id`
5. **Parse:** `parse_ubl_xml()` extracts metadata (invoice #, amounts, supplier OIB, lines) and embedded PDF (base64)
6. **Duplicate Check:** Query `Invoice` table by invoice_number + sender_oib
7. **Save:** Create `Invoice` record, write XML to `data/xmls/`, extract PDF to `data/pdfs/`
8. **Supplier Tracking:** Call `get_or_create_supplier_mapping()` to auto-create unmapped entry in `SupplierMapping`

**Supplier & Product Mapping (Stage 2 prep):**

1. **Supplier Selection:** Admin navigates to Settings → Supplier Mapping table
2. **Dodois Sync:** Click "Sync Dodois Catalog" → `_sync_dodois_catalog()` → Dodois API `/Accounting/v1/Suppliers`
3. **Link Supplier:** Select a row, choose Dodois supplier from dropdown, toggle "Enable upload"
4. **Auto-save:** Changes written to `SupplierMapping.dodois_catalog_id` and `.enabled`
5. **Product Mapping:** When supplier linked, product subsection appears
6. **Map Products:** Add mapping rows (eRačun description/EAN → Dodois raw material ID)
7. **Search:** Uses exact EAN match first, then case-insensitive substring match on description

**State Management:**

- **Session state:** Streamlit `st.session_state` holds auth tokens and UI state (pagination, selections)
- **Database state:** All persistent data (invoices, mappings, sync logs) in PostgreSQL
- **File state:** XMLs and PDFs stored in local `data/` volumes (Docker) or filesystem

## Key Abstractions

**EracunCredentials:**
- Purpose: Bundle moj-eRačun authentication parameters
- Examples: `app/core/eracun_client.py`, lines 15-21
- Pattern: Simple dataclass holding username, password, company_id, software_id, company_bu

**EracunClient:**
- Purpose: Abstract moj-eRačun API v2 communication
- Examples: `app/core/eracun_client.py`, lines 42–200+
- Pattern: Stateful client with httpx connection, credential injection in every request payload
- Methods: `ping()`, `query_inbox()`, `receive()`, `notify_import()`, `update_process_status()`

**InvoiceSyncService:**
- Purpose: Orchestrate full sync workflow
- Examples: `app/core/invoice_sync.py`, lines 22–100+
- Pattern: Service class accepting eracun_client + session_factory, with single `sync()` method
- Returns: Dictionary with status, counts, error message

**UBLInvoice & UBLLineItem:**
- Purpose: Typed representation of parsed invoice
- Examples: `app/core/ubl_parser.py`, lines 39–69
- Pattern: Dataclasses with default values; XML parsing populates fields
- Contains: Amounts (with/without VAT), line items, supplier/buyer OIB, embedded PDF (base64), detected pizzeria

**SupplierMapping & ProductMapping:**
- Purpose: Create N-to-M relationship between eRačun suppliers and Dodois suppliers/materials
- Examples: `app/db/models.py`, lines 121–189
- Pattern: SQLAlchemy models with foreign keys and relationships
- Behavior: Auto-created unmapped entries on first invoice import; manual linking in UI

## Entry Points

**Web Application:**
- Location: `app/web/app.py`
- Triggers: `streamlit run app/web/app.py` (via Dockerfile CMD)
- Responsibilities:
  - Authentication (bcrypt password check against config.yaml users)
  - Sidebar navigation (Invoices, Upload XML, Settings)
  - Invoice list with filters, detail view with PDF preview
  - Supplier/product mapping UI
  - Admin settings (eRačun connection test, sync log history)

**Configuration:**
- Location: `config.yaml` (default) + optional `config.local.yaml` (overlay)
- Entry point: `app/core/config_loader.py`
- Loaded at: Streamlit app startup (cached with `@st.cache_resource`)
- Contains: eRačun credentials, users, dodois_suppliers config, pizzeria department IDs, storage paths

**Database Initialization:**
- Location: `app/db/models.py` — `init_db()`, `seed_all()`
- Triggers: On first Streamlit run via `get_db()` cache (line 164 in app.py)
- Creates: All SQLAlchemy table definitions
- Seeds: DodoisSupplierCatalog, SupplierMapping, DodoisRawMaterialCatalog from config.yaml

## Error Handling

**Strategy:** Try-catch blocks at integration points; errors logged and stored; UI displays user-friendly messages.

**Patterns:**

- **API Errors:** `EracunClient._post()` calls `resp.raise_for_status()` — exceptions bubble to `InvoiceSyncService.sync()`, caught and logged to `SyncLog.error_message`
- **Parser Errors:** `parse_ubl_xml()` handles missing fields with defaults; malformed XML caught in `render_upload_page()` try-except
- **File I/O Errors:** XML/PDF writing wrapped in try-catch; Streamlit shows error toast
- **Database Errors:** SQLAlchemy session errors caught by Streamlit error handling; user notified via `st.error()`
- **Configuration Errors:** Missing required fields logged at startup; connection test in Settings shows status

## Cross-Cutting Concerns

**Logging:**
- Framework: Python standard `logging` module
- Output: Prints to Streamlit/Docker logs
- Used in: `EracunClient`, `InvoiceSyncService`, `UBL parser`
- Example: `logger.info(f"POST {url}")` in eracun_client.py line 66

**Authentication:**
- Approach: bcrypt password verification
- Implementation: `_do_login()` callback (line 181 in app.py) validates username + hashed password from config.yaml
- Scope: Basic (not role-based permissions yet); admin role unlocks sync button, settings page

**Configuration Management:**
- Approach: YAML files with deep merge
- Implementation: `load_config()` merges config.yaml + config.local.yaml (if present)
- Pattern: Supports environment variable override (`ERACUN_CONFIG` env var)

**Storage Paths:**
- XMLs: `data/xmls/` (relative to /app in Docker)
- PDFs: `data/pdfs/` (relative to /app in Docker)
- Configured in: `config.yaml` under `storage` section
- Used by: `InvoiceSyncService` (write on sync), `render_upload_page()` (write on manual upload), `render_invoice_detail()` (read for preview)

---

*Architecture analysis: 2026-04-06*
