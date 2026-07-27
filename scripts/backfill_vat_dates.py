"""
Populate ``invoices.vat_date`` from the stored XMLs.

The VAT period of an incoming invoice is decided by the tax point — when the
supply actually happened — not by when the supplier wrote the invoice. See
``ubl_parser.resolve_vat_date`` for the rule and why it is that way.

``Base.metadata.create_all`` only creates missing tables, never missing
columns, so this script also adds the column when it is absent. Both steps are
idempotent: a second run adds nothing and reports zero changes.

Usage:
    python scripts/backfill_vat_dates.py            # dry-run
    python scripts/backfill_vat_dates.py --apply    # write changes
"""
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from sqlalchemy import inspect, text

from app.core.config_loader import load_config, get_database_url
from app.core.ubl_parser import parse_ubl_xml, resolve_vat_date
from app.db.models import get_engine, get_session_factory, Invoice


def ensure_column(engine, apply_changes: bool) -> bool:
    """Add invoices.vat_date when missing. Returns True if it exists after."""
    cols = {c["name"] for c in inspect(engine).get_columns("invoices")}
    if "vat_date" in cols:
        print("Column invoices.vat_date : present")
        return True
    if not apply_changes:
        print("Column invoices.vat_date : MISSING (would be added with --apply)")
        return False
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE invoices ADD COLUMN vat_date TIMESTAMP"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_invoices_vat_date "
            "ON invoices (vat_date)"))
    print("Column invoices.vat_date : ADDED")
    return True


def main(apply_changes: bool) -> int:
    cfg = load_config()
    storage = cfg.get("storage", {}) or {}
    xml_dir = Path(storage.get("xml_dir", "/app/data/xmls"))

    engine = get_engine(get_database_url(cfg))
    if not ensure_column(engine, apply_changes):
        print("\nRun again with --apply to add the column first.")
        return 1

    session = get_session_factory(engine)()
    invoices = (
        session.query(Invoice)
        .filter(Invoice.processing_status != "deleted")
        .order_by(Invoice.id)
        .all()
    )

    changed, unchanged, missing_xml, failed = [], 0, 0, []
    moved_month = []
    source = Counter()

    for inv in invoices:
        ubl = None
        if inv.xml_path:
            path = xml_dir / inv.xml_path
            if path.exists():
                try:
                    ubl = parse_ubl_xml(path.read_bytes())
                except Exception as exc:
                    failed.append((inv.id, inv.xml_path, repr(exc)[:70]))
            else:
                missing_xml += 1

        if ubl is not None:
            new_date = resolve_vat_date(ubl)
            if ubl.tax_point_date:
                source["TaxPointDate"] += 1
            elif ubl.delivery_date:
                source["ActualDeliveryDate"] += 1
            else:
                source["IssueDate (not stated)"] += 1
        else:
            # No XML to read — the issue date is all we have.
            new_date = inv.issue_date
            source["IssueDate (no XML)"] += 1

        if new_date == inv.vat_date:
            unchanged += 1
            continue

        changed.append((inv, inv.vat_date, new_date))
        if (inv.issue_date and new_date
                and new_date.strftime("%Y-%m") != inv.issue_date.strftime("%Y-%m")):
            moved_month.append((inv, new_date))
        if apply_changes:
            inv.vat_date = new_date

    print(f"Scanned        : {len(invoices)}")
    print(f"Set / updated  : {len(changed)}")
    print(f"Already correct: {unchanged}")
    print(f"XML missing    : {missing_xml}")
    print(f"Parse failures : {len(failed)}")
    print()
    print("VAT date taken from:")
    for k, v in source.most_common():
        print(f"   {k:<24} {v:>4}  {100.0 * v / max(len(invoices), 1):4.1f}%")
    print()
    print(f"Invoices whose VAT month differs from the issue month: {len(moved_month)}")
    if moved_month:
        print(f"{'id':>5}  {'invoice':<20} {'supplier':<28} "
              f"{'issue':<11} {'vat date':<11}")
        for inv, new_date in moved_month[:25]:
            print(f"{inv.id:>5}  {(inv.invoice_number or '')[:20]:<20} "
                  f"{(inv.sender_name or '')[:28]:<28} "
                  f"{inv.issue_date.strftime('%Y-%m-%d')} "
                  f"{new_date.strftime('%Y-%m-%d')}")
        if len(moved_month) > 25:
            print(f"   … and {len(moved_month) - 25} more")
    print()

    if failed:
        print("Parse failures:")
        for row in failed[:10]:
            print("  ", row)
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
