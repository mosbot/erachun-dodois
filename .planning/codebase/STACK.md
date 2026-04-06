# Technology Stack

**Analysis Date:** 2026-04-06

## Languages

**Primary:**
- Python 3.12 - Core application, all backend logic and web UI
- YAML - Configuration management (config.yaml)
- SQL - PostgreSQL database queries via SQLAlchemy ORM
- XML - UBL 2.1 invoice parsing (Croatian HR-CIUS standard)

## Runtime

**Environment:**
- Python 3.12-slim (containerized via Docker)
- Streamlit 1.41.1 - Web framework for UI

**Package Manager:**
- pip
- Lockfile: requirements.txt (present)

## Frameworks

**Core:**
- Streamlit 1.41.1 - Web UI framework for invoice portal and dashboard
- SQLAlchemy 2.0.36 - ORM for database models and queries
- FastAPI 0.115.6 - (listed in requirements but not yet used; for future API expansion)
- Uvicorn 0.34.0 - ASGI server (paired with FastAPI)

**Testing:**
- No test framework detected in requirements

**Build/Dev:**
- Alembic 1.14.0 - Database migration management
- APScheduler 3.10.4 - Background task scheduling (for auto-sync)

## Key Dependencies

**Critical:**
- psycopg2-binary 2.9.10 - PostgreSQL database driver (required for production DB)
- httpx 0.28.1 - Async HTTP client for eRačun API calls and Dodois REST API
- bcrypt 4.2.1 - Password hashing for user authentication
- lxml 5.3.0 - XML parsing (UBL 2.1 invoice extraction)
- pyyaml 6.0.2 - YAML config file parsing

**Infrastructure:**
- pandas 2.2.3 - Data manipulation and display in Streamlit tables
- python-dateutil 2.9.0 - Date/time parsing and manipulation

## Configuration

**Environment:**
- ERACUN_CONFIG env var points to config.yaml location (default: /app/config.yaml)
- config.yaml contains all settings: eRačun credentials, Dodois API details, users, suppliers, pizzerias
- config.local.yaml (optional overlay) - loaded and merged on top of config.yaml for local overrides
- .streamlit/config.toml - Streamlit-specific settings (headless mode, port 8501, theme colors)

**Build:**
- Dockerfile - Python 3.12-slim with system deps for build-essential and libpq-dev
- docker-compose.yaml - Orchestrates Streamlit app + PostgreSQL 16-alpine

## Platform Requirements

**Development:**
- Python 3.12+
- PostgreSQL 16 (or SQLite for local dev: sqlite:///./dev.db)
- Docker & Docker Compose (for containerized deployment)

**Production:**
- Docker runtime
- PostgreSQL 16+ database
- VPS or container orchestration platform (Kubernetes, etc.)
- HTTPS via nginx reverse proxy with Let's Encrypt (planned)

## Database

**Primary:**
- PostgreSQL 16-alpine (containerized)
- Connection: `postgresql://eracun:eracun_secret@postgres:5432/e_rachun_dodois` (in docker-compose)
- Models: `app/db/models.py` defines all tables via SQLAlchemy declarative base

**Tables:**
- `invoices` - Incoming invoices from eRačun (electronic_id, document_nr, amounts, status)
- `sync_log` - Audit trail of sync operations
- `dodois_supplier_catalog` - Cached list of suppliers from Dodois API
- `supplier_mappings` - Maps eRačun OIB to Dodois supplier GUID
- `dodois_raw_material_catalog` - Cached raw materials per Dodois supplier
- `product_mappings` - Maps invoice lines to Dodois raw materials

---

*Stack analysis: 2026-04-06*
