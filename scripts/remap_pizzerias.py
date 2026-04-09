"""
Re-run pizzeria auto-detection on every invoice that has a stored XML.

When ``_detect_pizzeria`` in ``app/core/ubl_parser.py`` learns a new rule
(e.g. commit 4599a00 added OrderReference/ID as a hint source), previously-
loaded invoices stay on their old value. This script re-parses every
stored XML, reapplies detection, and updates ``invoice.dodois_pizzeria``.

Safety:
  * Invoices already uploaded to Dodois (``dodois_supply_id`` not NULL) are
    never touched — they are reported as SKIPPED.
  * Runs in dry-run mode by default; pass ``--apply`` to commit changes.

Usage:
    python scripts/remap_pizzerias.py            # dry-run
    python scripts/remap_pizzerias.py --apply    # write changes
"""
import sys
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from app.core.config_loader import load_config, get_database_url
from app.core.ubl_parser import parse_ubl_xml
from app.db.models import get_engine, get_session_factory, Invoice


def main(apply_changes: bool) -> int:
    cfg = load_config()
    storage = cfg.get("storage", {}) or {}
    xml_dir = Path(storage.get("xml_dir", "/app/data/xmls"))

    engine = get_engine(get_database_url(cfg))
    Session = get_session_factory(engine)
    session = Session()

    invoices = session.query(Invoice).filter(Invoice.xml_path.isnot(None)).all()

    total = len(invoices)
    changed = []
    cleared = []
    unchanged = 0
    skipped_uploaded = 0
    missing_xml = 0
    parse_errors = 0

    for inv in invoices:
        xml_path = xml_dir / inv.xml_path
        if not xml_path.exists():
            missing_xml += 1
            continue

        try:
            ubl = parse_ubl_xml(xml_path.read_text(encoding="utf-8"))
        except Exception as e:
            parse_errors += 1
            print(f"  ! parse failed for {inv.invoice_number} ({inv.xml_path}): {e}")
            continue

        detected = ubl.delivery_pizzeria
        current = inv.dodois_pizzeria

        if detected == current:
            unchanged += 1
            continue

        if inv.dodois_supply_id:
            skipped_uploaded += 1
            print(
                f"  = SKIP uploaded {inv.invoice_number} "
                f"({inv.sender_name}): current={current!r}, detected={detected!r}"
            )
            continue

        if detected is None:
            cleared.append((inv, current))
        else:
            changed.append((inv, current, detected))

        if apply_changes:
            inv.dodois_pizzeria = detected

    print()
    print(f"Total invoices with XML: {total}")
    print(f"  unchanged:         {unchanged}")
    print(f"  would update:      {len(changed)}")
    print(f"  would clear:       {len(cleared)} (detected None)")
    print(f"  skipped uploaded:  {skipped_uploaded}")
    print(f"  missing XML file:  {missing_xml}")
    print(f"  parse errors:      {parse_errors}")

    if changed:
        print()
        print("== Updates ==")
        for inv, old, new in changed:
            print(
                f"  {inv.invoice_number:<25} {inv.sender_name[:35]:<35} "
                f"{old!r} -> {new!r}"
            )
    if cleared:
        print()
        print("== Would clear (current non-null, detected None) ==")
        for inv, old in cleared:
            print(
                f"  {inv.invoice_number:<25} {inv.sender_name[:35]:<35} "
                f"{old!r} -> None"
            )

    if apply_changes:
        session.commit()
        print()
        print("Changes committed.")
    else:
        print()
        print("Dry-run only. Re-run with --apply to commit changes.")

    session.close()
    return 0


if __name__ == "__main__":
    apply = "--apply" in sys.argv[1:]
    sys.exit(main(apply))
