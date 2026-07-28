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
