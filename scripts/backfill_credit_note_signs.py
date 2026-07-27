"""
Re-parse stored XMLs and fix the stored totals of credit notes (odobrenje).

Credit notes arrive as UBL ``CreditNote`` documents. Until the parser learned
to recognise them, their amounts were stored exactly as the XML spelled them:
negative for suppliers that pre-sign (Pivac, METRO) but *positive* for those
that follow UBL and let the document type carry the meaning (STANIĆ,
Inter Alfa). Positive credit notes inflate every total in the invoice list and
the VAT report instead of reducing it.

This script re-parses every stored XML and rewrites ``total_without_vat`` /
``total_vat`` / ``total_with_vat`` for documents the parser now identifies as
credit notes. Regular invoices are never modified.

Safety:
  * Only rows whose XML parses as a CreditNote are considered.
  * Only writes when the stored value actually differs.
  * Runs in dry-run mode by default; pass ``--apply`` to commit changes.
  * Idempotent — a second run reports zero changes.

Usage:
    python scripts/backfill_credit_note_signs.py            # dry-run
    python scripts/backfill_credit_note_signs.py --apply    # write changes
"""
import sys
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from app.core.config_loader import load_config, get_database_url
from app.core.ubl_parser import parse_ubl_xml
from app.db.models import get_engine, get_session_factory, Invoice

FIELDS = ("total_without_vat", "total_vat", "total_with_vat")


def main(apply_changes: bool) -> int:
    cfg = load_config()
    storage = cfg.get("storage", {}) or {}
    xml_dir = Path(storage.get("xml_dir", "/app/data/xmls"))

    engine = get_engine(get_database_url(cfg))
    Session = get_session_factory(engine)
    session = Session()

    invoices = (
        session.query(Invoice)
        .filter(Invoice.xml_path.isnot(None))
        .filter(Invoice.processing_status != "deleted")
        .order_by(Invoice.id)
        .all()
    )

    changed = []
    already_ok = 0
    missing_xml = 0
    parse_failed = []
    uploaded_warn = []

    for inv in invoices:
        path = xml_dir / inv.xml_path
        if not path.exists():
            missing_xml += 1
            continue
        try:
            ubl = parse_ubl_xml(path.read_bytes())
        except Exception as exc:
            parse_failed.append((inv.id, inv.xml_path, repr(exc)[:80]))
            continue

        if not ubl.is_credit_note:
            continue

        new_values = {
            "total_without_vat": ubl.total_without_vat,
            "total_vat": ubl.total_vat,
            "total_with_vat": ubl.total_with_vat,
        }
        deltas = {
            f: (getattr(inv, f) or 0.0, new_values[f])
            for f in FIELDS
            if abs((getattr(inv, f) or 0.0) - new_values[f]) > 0.005
        }
        type_stale = inv.document_type_id != 381

        if not deltas and not type_stale:
            already_ok += 1
            continue

        if inv.dodois_supply_id:
            uploaded_warn.append((inv.id, inv.invoice_number))

        changed.append((inv, deltas, type_stale))
        if apply_changes:
            for field, value in new_values.items():
                setattr(inv, field, value)
            inv.document_type_id = 381
            inv.document_type_name = "Odobrenje"

    print(f"Scanned            : {len(invoices)}")
    print(f"Credit notes OK    : {already_ok}")
    print(f"Credit notes fixed : {len(changed)}")
    print(f"XML missing        : {missing_xml}")
    print(f"Parse failures     : {len(parse_failed)}")
    print()

    if changed:
        print(f"{'id':>5}  {'invoice':<20} {'supplier':<30} "
              f"{'gross old':>11} {'gross new':>11}")
        for inv, deltas, type_stale in changed:
            old, new = deltas.get(
                "total_with_vat", (inv.total_with_vat, inv.total_with_vat))
            flag = "  [type->381]" if type_stale else ""
            print(f"{inv.id:>5}  {(inv.invoice_number or '')[:20]:<20} "
                  f"{(inv.sender_name or '')[:30]:<30} "
                  f"{old:>11.2f} {new:>11.2f}{flag}")
        print()
        swing = sum(
            d["total_with_vat"][1] - d["total_with_vat"][0]
            for _, d, _ in changed if "total_with_vat" in d
        )
        print(f"Reported gross total moves by: {swing:+.2f} EUR")
        print()

    if uploaded_warn:
        print("WARNING — credit notes that carry a Dodois supply id "
              "(check them in Office Manager):")
        for inv_id, number in uploaded_warn:
            print(f"  id={inv_id} {number}")
        print()

    if parse_failed:
        print("Parse failures:")
        for row in parse_failed:
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
