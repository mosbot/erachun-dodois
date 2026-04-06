# Codebase Structure

**Analysis Date:** 2026-04-06

## Directory Layout

```
e-rachun-dodois/
├── app/                         # Main application package
│   ├── __init__.py
│   ├── core/                    # Business logic & external integrations
│   │   ├── __init__.py
│   │   ├── config_loader.py     # YAML config with overlay support
│   │   ├── eracun_client.py     # moj-eRačun API v2 client
│   │   ├── invoice_sync.py      # Sync orchestration service
│   │   └── ubl_parser.py        # UBL 2.1 XML parsing
│   ├── db/                      # Database models & initialization
│   │   ├── __init__.py
│   │   └── models.py            # SQLAlchemy models + seed functions
│   └── web/                     # Web UI (Streamlit)
│       ├── __init__.py
│       └── app.py               # Streamlit application (all pages)
├── data/                        # Data storage (Docker volumes)
│   ├── pdfs/                    # Extracted PDFs
│   └── xmls/                    # Downloaded/uploaded invoice XMLs
├── docker/                      # Docker build artifacts (nginx, postgres configs)
│   ├── nginx/
│   └── postgres/
├── scripts/                     # Utility scripts
│   └── gen_password.py          # Generate bcrypt password hashes
├── .streamlit/                  # Streamlit theme config
│   └── config.toml
├── .planning/                   # GSD documentation
│   └── codebase/
├── config.yaml                  # Main configuration file
├── config.local.yaml            # (optional) Local overrides
├── Dockerfile                   # Python 3.12 Streamlit image
├── docker-compose.yaml          # Postgres + Streamlit services
├── requirements.txt             # Python dependencies
└── CLAUDE.md                    # Project context
```

## Directory Purposes

**app/:**
- Purpose: Core application code
- Contains: Python packages for web, database, and external integrations
- Key files: `app/web/app.py` (entry point)

**app/core/:**
- Purpose: Business logic independent of web framework
- Contains: API clients, sync services, XML parsing, configuration management
- Key files:
  - `eracun_client.py` — moj-eRačun API wrapper
  - `invoice_sync.py` — Synchronization workflow orchestrator
  - `ubl_parser.py` — XML to Python object mapper
  - `config_loader.py` — Configuration file loader with merge support

**app/db/:**
- Purpose: Data persistence layer
- Contains: SQLAlchemy ORM models, database initialization, seed data
- Key files:
  - `models.py` — All table definitions (Invoice, SyncLog, DodoisSupplierCatalog, SupplierMapping, DodoisRawMaterialCatalog, ProductMapping)

**app/web/:**
- Purpose: User interface and web framework integration
- Contains: Single Streamlit application with all pages
- Key files:
  - `app.py` — Authentication, navigation, all UI pages (Invoices, Upload XML, Settings)

**data/:**
- Purpose: Runtime file storage (excluded from git)
- Contains: Downloaded invoice XMLs and extracted PDFs
- Generated: Yes
- Committed: No (Docker volumes)

**docker/:**
- Purpose: Container orchestration support files
- Contains: nginx config (for Stage 2 reverse proxy), PostgreSQL init scripts
- Generated: No
- Committed: Yes

**scripts/:**
- Purpose: Administrative utilities
- Key files: `gen_password.py` — Generate bcrypt hashes for config.yaml user passwords

**.streamlit/:**
- Purpose: Streamlit theme and platform configuration
- Key files: `config.toml` — Color scheme (orange theme), layout settings

## Key File Locations

**Entry Points:**
- `app/web/app.py` — Streamlit application entry point (1029 lines)
- `Dockerfile` — Python 3.12 image builder
- `docker-compose.yaml` — Service orchestration (web + PostgreSQL)

**Configuration:**
- `config.yaml` — Primary configuration (eRačun credentials, users, suppliers, pizzeria IDs)
- `config.local.yaml` — Local environment overrides
- `.streamlit/config.toml` — Streamlit theme settings
- `requirements.txt` — Python dependencies

**Core Logic:**
- `app/core/eracun_client.py` — moj-eRačun API v2 client (httpx-based)
- `app/core/invoice_sync.py` — Sync service orchestrating download → parse → save
- `app/core/ubl_parser.py` — UBL 2.1 XML parser for HR-CIUS invoices
- `app/core/config_loader.py` — YAML config with overlay merge support

**Database:**
- `app/db/models.py` — SQLAlchemy models + initialization + seed functions (346 lines)

## Naming Conventions

**Files:**
- Python modules: `snake_case` (`eracun_client.py`, `ubl_parser.py`, `config_loader.py`)
- Packages: Directories with `__init__.py` (`app/`, `app/core/`, `app/db/`, `app/web/`)

**Classes:**
- `PascalCase` with domain suffix:
  - `EracunClient` — API client
  - `EracunCredentials` — Auth bundle (dataclass)
  - `InvoiceSyncService` — Orchestration service
  - `UBLInvoice`, `UBLLineItem` — Domain dataclasses
  - `Invoice`, `SyncLog`, `DodoisSupplierCatalog`, `SupplierMapping`, `ProductMapping` — ORM models

**Functions:**
- Public: `snake_case` (`parse_ubl_xml`, `load_config`, `get_or_create_supplier_mapping`, `render_invoices_page`)
- Private: `_leading_underscore` (`_do_login`, `_post`, `_text`, `_sync_dodois_catalog`, `_unit_label`)

**Variables:**
- Local/instance: `snake_case` (`search_text`, `supplier_filter`, `current_idx`)
- Constants: `UPPERCASE` (`NS`, `CONFIG_PATH`, `_METRO_RAW_MATERIALS`)
- Special: `st.session_state`, `st.column_config` (Streamlit framework)

**Module-level patterns:**
- Avoid wildcard imports; import explicitly
- Group imports: stdlib → external → local
- Use path manipulation for relative imports (see app.py lines 16-18)

## Where to Add New Code

**New API Integration (e.g., Dodois REST calls):**
- File: `app/core/dodois_client.py` (create new, similar to eracun_client.py)
- Config: Add section to `config.yaml` with API endpoint, token/credentials
- Entry: Import and instantiate in `app/web/app.py` on demand

**New Database Model:**
- File: Add class to `app/db/models.py` with appropriate `__tablename__`, columns, relationships
- Seed: Add function to `seed_all()` if needs initial data
- Usage: Import and use in web layer via session factory

**New UI Page:**
- File: Add function to `app/web/app.py` (e.g., `render_new_page()`)
- Navigation: Add radio option in `render_sidebar()` (around line 245)
- Route: Handle page selection in `main()` (lines 1011-1022)

**New Utility Module:**
- File: `app/core/product_matcher.py` (for EAN/name matching logic)
- File: `app/core/price_calculator.py` (for Dodois price formulas)
- File: `app/core/helpers.py` (for shared parsing/validation functions)
- Import: Use in relevant layer (core for business logic, web for UI helpers)

**New Configuration Section:**
- File: Add to `config.yaml` structure
- Access: Use `cfg.get("section_name", {})` pattern in `config_loader.py` or web app
- Override: Automatically merged from `config.local.yaml` if present

## Special Directories

**data/:**
- Purpose: Runtime invoice storage
- Generated: Yes (created on first sync/upload)
- Committed: No (Docker volume)

**docker/:**
- Purpose: Docker configuration and images
- Generated: No (version-controlled templates)
- Committed: Yes

**.planning/codebase/:**
- Purpose: GSD analysis documents (ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, CONCERNS.md, STACK.md, INTEGRATIONS.md)
- Generated: Yes (by gsd-map-codebase)
- Committed: Yes

**.venv/:**
- Purpose: Python virtual environment (dev only)
- Generated: Yes (via `python -m venv .venv`)
- Committed: No

## How Streamlit App Organization Works

**Entry point:** `streamlit run app/web/app.py`

**Architecture within app.py:**

1. **Page config & custom CSS** (lines 32–153): Set title, layout, define design system colors
2. **Config & DB loading** (lines 156–175): `@st.cache_resource` decorators for config, DB initialization
3. **Authentication** (lines 178–233): `_do_login()` callback + `authenticate()` function
4. **Sidebar** (lines 236–276): Navigation radio, sync button (admin only), logout
5. **Page renderers** (lines 279–962):
   - `render_invoices_page()` — List, filter, detail, PDF preview, pizzeria selector
   - `render_upload_page()` — File upload, XML parsing, duplicate check, save
   - `render_settings_page()` — eRačun connection test, supplier mapping UI, product mapping UI, sync history
6. **UI Components** (lines 661–897):
   - `render_supplier_mapping_section()` — Supplier table, link to Dodois catalog
   - `render_product_mapping_section()` — Product mapping table, material selector
7. **Integration helpers** (lines 899–961):
   - `_sync_dodois_catalog()` — Fetch suppliers from Dodois API
   - `_unit_label()` — Map unit codes to display strings
8. **Main** (lines 1008–1028): Check auth → render sidebar → dispatch to page

## Import Patterns Used

**Absolute imports (recommended):**
```python
from app.db.models import Invoice, SyncLog
from app.core.config_loader import load_config
from app.core.ubl_parser import parse_ubl_xml
```

**System path manipulation (for flexibility):**
```python
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, PROJECT_ROOT)
```

**Standard library first, then external, then local:**
```python
import os, sys, base64
from pathlib import Path
from datetime import datetime

import streamlit as st
import pandas as pd
import yaml
import bcrypt

from app.db.models import Invoice
from app.core.config_loader import load_config
```

---

*Structure analysis: 2026-04-06*
