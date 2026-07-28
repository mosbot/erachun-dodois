"""
Mark invoices that pdf2planfact already posted to PlanFact.

Run this once before the first live posting run. Without it the poster would
create a second copy of every invoice booked between 2025-12-31 and 2026-07-05,
because PlanFact does not reject duplicate externalIds.

Also adds the planfact_* columns when they are missing —
``Base.metadata.create_all`` only creates missing tables, never missing columns.

Ordering matters here, and it is not the obvious order:

1. If no API key is configured, the columns are still ensured (with
   --apply). Every ``session.query(Invoice)`` in the running app names all
   mapped columns, so on a freshly-deployed database the portal raises
   ``UndefinedColumn`` until the columns exist — an operator without the key
   configured yet must still be able to unbreak the app. The actual
   reconcile (matching invoices against PlanFact) is skipped in that case
   and nothing is marked as posted.
2. If a key IS configured, PlanFact's operations are fetched FIRST, before
   any schema change. Fetching touches no ORM/DDL, so if it fails (network,
   PlanFact outage, exhausted retries), the script exits non-zero having
   changed nothing at all. The columns are only added — and invoices only
   matched/marked — once the fetch has actually succeeded. Reversing this
   (add columns, then fetch) is the dangerous order: a failed fetch after
   the columns exist would leave every historical invoice unmarked while
   the poster's guard clause (which only checks for the columns' existence)
   is already satisfied, so the very next cron tick posts ~159 historical
   invoices as duplicates.

Exit codes:
    0 — reconcile completed (or dry-run preview completed)
    1 — API key not configured, or columns are missing and --apply was not
        passed (columns were left untouched in the latter case too, since
        ensure_columns() itself refuses to run DDL without --apply)
    2 — fetching operations from PlanFact failed. No schema change and no
        marking happened. THE CRON POSTER MUST NOT BE ALLOWED TO RUN until
        this completes successfully — see point 2 above.

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


def fetch_posted_operations(client: PlanfactClient, accounts: dict) -> dict:
    """Fetch every existing PlanFact operation for the configured accounts.

    Touches no ORM/DDL — this is deliberate, see the module docstring. The
    caller must treat any exception from this as fatal: schema changes and
    invoice marking must not proceed on a partial or failed fetch.

    Returns ``{(provider, externalId): operationId}``.
    """
    posted = {}
    for provider, account_id in accounts.items():
        ops = client.list_operations(account_id, "2025-01-01", "2030-12-31")
        for op in ops:
            ext = op.get("externalId")
            if ext:
                posted.setdefault((provider, str(ext)), str(op.get("operationId")))
        print(f"{provider}: {len(ops)} operations in PlanFact")
    return posted


def main(apply_changes: bool) -> int:
    cfg = load_config()
    pf = cfg.get("planfact", {}) or {}
    engine = get_engine(get_database_url(cfg))

    api_key = pf.get("api_key")
    if not api_key:
        # Columns must be addable even without a key configured, or an
        # operator who hasn't set one up yet cannot unbreak the portal
        # (see module docstring, point 1). The reconcile itself needs the
        # key and is skipped — nothing is matched or marked.
        ensure_columns(engine, apply_changes)
        print("PlanFact not configured (planfact.api_key missing) — "
              "reconcile (matching invoices against PlanFact) was skipped. "
              "No invoices were marked as posted. Configure planfact.api_key "
              "and re-run before the cron poster is allowed to run.")
        return 1

    client = PlanfactClient(api_key, base_url=pf.get(
        "base_url", "https://api.planfact.io/api/v1"))

    # Fetch FIRST, before any DDL/ORM writes — see module docstring, point 2.
    try:
        posted = fetch_posted_operations(client, pf.get("accounts") or {})
    except Exception as exc:
        print(f"\nRECONCILE DID NOT COMPLETE: failed to fetch operations "
              f"from PlanFact: {exc}")
        print("No schema changes were made and no invoices were marked. "
              "The cron poster (scripts/post_to_planfact.py) must NOT be "
              "allowed to run until this reconcile completes successfully "
              "— otherwise it will treat every already-posted historical "
              "invoice as new and create duplicates in PlanFact.")
        return 2

    if not posted:
        print(f"\nRECONCILE DID NOT COMPLETE: PlanFact returned no operations.")
        print(f"Approximately 126 already-posted invoices are expected to exist.")
        print("No schema changes were made and no invoices were marked. "
              "The cron poster (scripts/post_to_planfact.py) must NOT be "
              "allowed to run until this reconcile completes successfully "
              "— otherwise it will treat every already-posted historical "
              "invoice as new and create duplicates in PlanFact.")
        return 2

    if not ensure_columns(engine, apply_changes):
        print("\nRun again with --apply to add the columns first.")
        return 1

    session = get_session_factory(engine)()
    xml_dir = Path(get_storage_config(cfg).get("xml_dir", "/app/data/xmls"))

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
