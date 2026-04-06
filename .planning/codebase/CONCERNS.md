# Codebase Concerns

**Analysis Date:** 2026-04-06

## Security & Secrets

**Credentials in config.yaml:**
- Issue: All sensitive data (eRačun credentials, Dodois API token, database password) are stored in plaintext in `config.yaml`
- Files: `config.yaml` (lines 6-13, 200-202), `docker-compose.yaml` (line 26)
- Impact: If repo is accidentally committed with credentials filled, they become world-accessible. Docker-compose hardcodes DB password.
- Fix approach: Move all secrets to environment variables (load via `os.environ` in `app/core/config_loader.py`). Use Docker secrets or vault in production. Add `.gitignore` entry for `config.local.yaml`.

**Dodois Bearer token management:**
- Issue: Users must manually extract token from browser DevTools and paste into config (line 200-202)
- Files: `app/web/app.py` (lines 902-912), `config.yaml` (line 202)
- Impact: Tokens readable by anyone with file access. No token rotation/expiry mechanism.
- Fix approach: Implement secure token storage in DB with encryption. Add token refresh capability. Display warning about token age.

**Password hashing in config.yaml:**
- Issue: User password hashes stored in config.yaml (line 28), mounted read-only in Docker (line 10)
- Files: `config.yaml` (lines 24-35)
- Impact: No runtime password changes. Config rebuild required for user management.
- Fix approach: Move users to PostgreSQL. Implement password change UI. Use env vars for initial admin user only.

---

## Missing Infrastructure (Stage 2 Blocker)

**No Dodois client implementation:**
- Issue: `dodois_client.py` does not exist but required for Stage 2 invoice uploads
- Files: Missing entirely
- Blocks: Invoice upload to Dodois, product line matching, price calculations, supply creation
- Fix approach: Create `app/core/dodois_client.py` with methods for fetching raw materials, creating supplies (POST to `/Accounting/v1/incomingstock/supplies`), UUID generation, price calculations per CLAUDE.md, duplicate detection by invoice number.

**Missing background scheduler:**
- Issue: APScheduler in `requirements.txt` (line 12) but never instantiated or used
- Files: `requirements.txt` (line 12), never imported in app
- Impact: Auto-sync feature (config.yaml line 17) cannot work without scheduler
- Fix approach: Create APScheduler task in app startup. Trigger `InvoiceSyncService.sync()` at configured interval (default 30 min).

**Hardcoded supplier data:**
- Issue: METRO products hardcoded in `models.py` (lines 197-214) as seed data
- Files: `app/db/models.py` (lines 197-214)
- Impact: Adding suppliers requires code changes. Product lists cannot be updated from Dodois without modifying Python.
- Fix approach: Load supplier products from Dodois API on first sync. Extend `_sync_dodois_catalog()` in `app/web/app.py` (lines 902-961) to fetch raw materials.

---

## Data & Duplicate Handling

**Weak duplicate detection:**
- Issue: Duplicates checked by invoice_number + sender_oib only (invoice_sync.py lines 200-207)
- Files: `app/core/invoice_sync.py` (lines 200-207), `app/web/app.py` (lines 510-524)
- Impact: Same invoice re-downloaded/re-uploaded creates duplicate records. Dodois API may reject duplicates.
- Fix approach: Add unique constraint on (invoice_number, sender_oib). Check Dodois for existing supplies before creating. Use soft-delete (processing_status = "deleted") instead of hard delete.

**Invoice number format variations:**
- Issue: CLAUDE.md warns eRačun uses '2315/11/6005' but Dodois manual entry uses '6/0(011)0005/002315'
- Files: `app/core/invoice_sync.py` (lines 154, 217), `app/web/app.py` (line 363)
- Impact: Manual uploads and auto-synced invoices may have incompatible formats for Dodois
- Fix approach: Normalize invoice numbers to canonical format before Dodois upload. Document expected format.

**Line item aggregation not implemented:**
- Issue: CLAUDE.md states eRačun invoices may have duplicate lines (same product qty=1 repeated). Not handled in parser or sync.
- Files: Not in `app/core/ubl_parser.py` or `app/core/invoice_sync.py`
- Impact: METRO invoice with 5 lines for same product becomes 5 supply lines instead of 1 aggregated. Dodois rejects or calculates incorrectly.
- Fix approach: Create aggregation logic in dodois_client.py. Group by product ID (EAN first, then name). Sum quantities. Recalculate totals.

---

## Performance & Scalability

**Monolithic Streamlit app (1028 lines):**
- Issue: `app/web/app.py` is 1028 lines mixing UI rendering, business logic, DB queries, API calls
- Files: `app/web/app.py`
- Impact: Hard to test. Performance degrades with invoice count (no pagination). Difficult to add pages.
- Fix approach: Extract pages into modules (`app/web/pages/invoices.py`, `app/web/pages/settings.py`, `app/web/pages/upload.py`). Move business logic to service classes. Add pagination. Implement query caching.

**No pagination for invoice list:**
- Issue: Invoice list query at line 338 loads all matching invoices into memory
- Files: `app/web/app.py` (lines 318-338)
- Impact: Thousands of invoices = excessive memory. UI becomes slow. Export fails.
- Fix approach: Implement page-based pagination (50 invoices/page). Add export button for filtered results.

**No database indexes for common queries:**
- Issue: Queries lack indexes for filtering by status, sender_name, dates, dodois_catalog_id
- Files: `app/db/models.py` (missing indexes), `app/web/app.py` (query patterns at 285-338, 675-679)
- Impact: Queries slow significantly as invoice count grows
- Fix approach: Add indexes on (processing_status), (sender_name, issue_date), (dodois_catalog_id), (enabled).

---

## Fragile Areas & Technical Debt

**PDF extraction depends on XML structure:**
- Issue: PDF extracted from UBL XML by finding `AdditionalDocumentReference/Attachment/EmbeddedDocumentBinaryObject` (ubl_parser.py lines 199-205)
- Files: `app/core/ubl_parser.py` (lines 199-205)
- Impact: If supplier uses different XML structure or no PDF, extraction silently fails. processing_status remains "parsed" without PDF.
- Fix approach: Log warnings when PDF not found. Consider fallback to eRačun API PDF download. Make PDF optional.

**UBL parser accepts malformed data:**
- Issue: Parser returns UBLInvoice with default/zero values if fields missing (ubl_parser.py uses `_float()` returning 0.0, `_text()` returning None)
- Files: `app/core/ubl_parser.py` (lines 270-276, 262-267)
- Impact: Malformed invoices with missing amounts pass validation. Saved to DB with zero totals. Dodois upload fails or succeeds silently with wrong amounts.
- Fix approach: Add validation (total_with_vat > 0, supplier_oib not empty). Return validation errors to UI. Reject invoices with missing required fields.

**Pizzeria detection is brittle:**
- Issue: Pizzeria detection (ubl_parser.py lines 210-246) uses substring matching: "TRATIN" → Zagreb-1, "MAKSIMIR" → Zagreb-2
- Files: `app/core/ubl_parser.py` (lines 210-246)
- Impact: Different address formats fail detection silently (returns None). Users manually select pizzeria (app.py lines 397-414).
- Fix approach: Use fuzzy matching (difflib) instead of substring. Add configurable detection rules in config.yaml. Log detection attempts.

**Raw material matching is manual:**
- Issue: Product mapping UI (app.py lines 777-892) requires manual linkage of eRačun descriptions to Dodois raw materials
- Files: `app/web/app.py` (lines 777-892)
- Impact: Scale barrier for new suppliers. Requires human review of every product. EAN matching optional and not prioritized.
- Fix approach: Implement EAN-first matching with fuzzy fallback. Auto-create mappings when EAN matches catalog. Add batch import from spreadsheet.

**Dodois catalog not refreshed automatically:**
- Issue: "Sync Dodois Catalog" button (app.py line 667) fetches suppliers only, not raw materials
- Files: `app/web/app.py` (lines 902-961)
- Impact: Raw materials stay hardcoded. If Dodois adds/removes products, local catalog becomes stale.
- Fix approach: Extend `_sync_dodois_catalog()` to fetch raw materials via `/Accounting/v1/incomingstock/departments/{dept}/suppliers/{supplier}/rawmaterials`. Schedule automatic refreshes (daily/weekly). Version catalog entries with synced_at.

---

## API Client Issues

**EracunClient.receive() response parsing is fragile:**
- Issue: Lines 138-143 handle multiple response formats (string, dict with "Document"/"Xml", or fallback to str(data))
- Files: `app/core/eracun_client.py` (lines 138-143)
- Impact: If eRačun API changes format, fallback-to-str(data) returns unparseable JSON instead of XML
- Fix approach: Add response logging for debugging. Validate response contains valid XML. Raise explicit error if format unrecognized.

**No retry logic for flaky API calls:**
- Issue: EracunClient and DodoisClient (non-existent) make HTTP calls without retries or circuit breaker
- Files: `app/core/eracun_client.py` (lines 67-73, 154-161, 180-183)
- Impact: Network blip during sync causes entire sync to fail. Progress is lost.
- Fix approach: Implement exponential backoff retry (3-5 attempts) for transient failures. Log and continue on permanent failures.

**No timeout protection for long-running sync:**
- Issue: Sync can take indefinite time if eRačun API hangs (large number of invoices)
- Files: `app/core/invoice_sync.py` (lines 39-119), `app/core/eracun_client.py` (line 48)
- Impact: Long-running syncs block Streamlit UI. Timeout in production.
- Fix approach: Implement per-request timeout override. Add progress updates during sync. Run sync in background thread with cancellation support.

---

## Incomplete Configuration

**Zagreb-2 department not filled:**
- Issue: `config.yaml` line 210 has `department_id: ""  # TODO: fill in`
- Files: `config.yaml` (lines 209-211)
- Impact: Cannot upload to Zagreb-2. User selects Zagreb-2 in UI → Dodois API fails with 400 Bad Request.
- Fix approach: Contact Dodois support for Zagreb-2 department_id/unit_id. Document process in README.

**Disabled suppliers have empty eracun_name:**
- Issue: Lines 57-138 in config.yaml have suppliers with `eracun_name: ""` (marked TODO)
- Files: `config.yaml` (lines 57-138)
- Impact: When new invoices from disabled suppliers arrive, SupplierMapping.eracun_name stays empty. Supplier list becomes confusing.
- Fix approach: Use eRačun API to fetch actual sender names. Document how to discover eracun_name. Auto-update eracun_name from invoice when first received.

**Missing eRačun API credentials:**
- Issue: `config.yaml` lines 9-12 have placeholders: `username: ""`, `password: ""`, `software_id: ""`
- Files: `config.yaml` (lines 9-12)
- Impact: Until credentials obtained from integracije@moj-eracun.hr, eRačun sync cannot work
- Fix approach: Add status check in Settings page with clear instructions on requesting credentials.

---

## Test Coverage Gaps

**No test suite:**
- Issue: No `.test`, `.spec`, or `tests/` directory found. Entire app lacks unit/integration tests.
- Files: Entire app
- Blocks: Refactoring without regression risk, validating price calculations, testing product matching, verifying XML parsing edge cases
- Fix approach: Add pytest with fixtures: `tests/unit/test_ubl_parser.py` (XML parsing), `tests/unit/test_eracun_client.py` (mock API), `tests/unit/test_price_calculator.py` (CLAUDE.md formulas), `tests/integration/test_invoice_sync.py` (sync flow).

---

## Logging & Observability

**Insufficient logging for debugging:**
- Issue: Sparse logging. invoice_sync.py has logger but minimal. app.py has no logging.
- Files: `app/core/invoice_sync.py`, `app/web/app.py`
- Impact: Hard to diagnose failures. Dodois upload errors silent (Stage 2).
- Fix approach: Add structured logging (JSON) at INFO level for major operations. DEBUG level for API requests (sanitize credentials). Add correlation IDs for tracing invoices. Send logs to file/external service.

**No error alerting:**
- Issue: Sync or invoice upload failures do not alert admin. No Slack/email/webhook integration.
- Files: Entire stack
- Impact: Admin may not notice failed imports for days
- Fix approach: Add webhook integration (HTTP POST to Slack) on sync failures and upload errors.

---

## Deployment & Operations

**Hardcoded storage paths:**
- Issue: `storage.pdf_dir` and `storage.xml_dir` default to `/app/data/pdfs` and `/app/data/xmls` (config_loader.py lines 97-98)
- Files: `app/core/config_loader.py` (lines 96-99), `docker-compose.yaml` (line 12)
- Impact: Local dev and Docker volumes use same paths. If config.yaml lost, storage path unknown.
- Fix approach: Make paths configurable via environment variables with Docker volume mount as fallback.

**No database migrations:**
- Issue: Schema created by SQLAlchemy `Base.metadata.create_all()` on startup (models.py line 344). Alembic in requirements.txt (line 7) but unused.
- Files: `app/db/models.py` (lines 333-345)
- Impact: Cannot add columns or change schemas without manual DB surgery
- Fix approach: Initialize Alembic. Create initial migration from current schema. Document migration process. Add schema upgrade to deployment checklist.

**No graceful shutdown for Streamlit:**
- Issue: Streamlit has no shutdown hooks. DB connections may not close properly.
- Files: `app/web/app.py` (session handling at 259, 283, 418, 500, 623, 645, 672, 961)
- Impact: Connection pool exhaustion if app restarts frequently. Orphaned connections.
- Fix approach: Add cleanup handlers via `st.session_state.on_change` or context managers for all session usage.

---

## Known Limitations (By Design)

**Manual pizzeria selection in most cases:**
- Issue: Auto-detection works only if supplier includes "TRATINSKA" or "MAKSIMIR" in delivery address
- Files: `app/core/ubl_parser.py` (lines 210-246)
- Workaround: User manually selects pizzeria in UI (app.py lines 397-414)
- Note: Acceptable for MVP. Improve detection before Stage 2 full rollout.

**No supplier OIB validation:**
- Issue: OIB parsing (ubl_parser.py lines 249-259) never validates format (should be 11 digits)
- Files: `app/core/ubl_parser.py` (lines 249-259)
- Impact: Invalid OIBs saved to DB, breaking Dodois lookups
- Fix approach: Add OIB format validation (regex: `^\d{11}$` after cleaning).

---

## Priority Roadmap

**Critical (blocks Stage 2):**
1. Implement `app/core/dodois_client.py` with supply creation and price calculation
2. Implement line item aggregation for METRO invoices
3. Get eRačun API credentials from integracije@moj-eracun.hr
4. Get Zagreb-2 department_id/unit_id from Dodois support
5. Move secrets from config.yaml to environment variables

**High (affects production stability):**
1. Add database migration strategy with Alembic
2. Add comprehensive error logging and alerting
3. Implement retry logic for API calls
4. Add product matching tests and EAN-first matching logic
5. Add unique constraint on (invoice_number, sender_oib) with soft-delete

**Medium (improves UX):**
1. Add pagination to invoice list
2. Extract Streamlit app into modules
3. Implement pizzeria detection improvements
4. Auto-refresh Dodois catalog (suppliers + raw materials)
5. Add user management UI (passwords in DB instead of config)

**Low (nice to have):**
1. Add test suite with pytest
2. Implement background scheduler for auto-sync
3. Add Telegram bot (Stage 2 feature)
4. Add invoice approval workflow
5. Add financial reporting dashboard

---

*Concerns audit: 2026-04-06*
