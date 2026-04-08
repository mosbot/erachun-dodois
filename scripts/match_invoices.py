"""
Match eRačun invoices with Dodois supplies to auto-populate ProductMapping.

Usage:
    python scripts/match_invoices.py [--dry-run] [--from DATE] [--to DATE]

Options:
    --dry-run     Show what would be written without touching DB
    --from DATE   Start date (default: 2025-01-01)
    --to DATE     End date (default: today)
"""
import argparse
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def dodois_to_eracun(inv_number: str) -> str:
    """
    Convert Dodois invoice number to eRačun format.
    Dodois: 6/0(011)0003/004488  →  eRačun: 4488/11/6003
    Formula: C/0(0BB)0YYY/00000A  →  A/BB/CYYY
    If unrecognised, return unchanged.
    """
    m = re.match(r"^(\d)/0\(0(\d+)\)0(\d{3})/0*(\d+)$", inv_number)
    if m:
        c, bb, yyy, a = m.groups()
        return f"{int(a)}/{int(bb)}/{c}{yyy}"
    return inv_number


def _line_value(line, *keys):
    """Get first non-None value from a dict or object for the given keys."""
    for key in keys:
        v = line.get(key) if isinstance(line, dict) else getattr(line, key, None)
        if v is not None:
            return v
    return None


def aggregate_ubl_lines(lines: list) -> list:
    """
    Merge duplicate UBL lines (same description, sum qty and line_total).
    Accepts UBLLineItem objects or dicts with keys: description/item_name, quantity, line_total.
    Returns list of dicts: {description, quantity, line_total}.
    """
    grouped: dict = {}
    for line in lines:
        desc = (_line_value(line, "item_name", "description") or "").strip()
        if not desc:
            continue
        qty = float(_line_value(line, "quantity") or 0)
        total = float(_line_value(line, "line_total") or 0)
        if desc in grouped:
            grouped[desc]["quantity"] += qty
            grouped[desc]["line_total"] = round(grouped[desc]["line_total"] + total, 4)
        else:
            grouped[desc] = {"description": desc, "quantity": qty, "line_total": round(total, 4)}
    return list(grouped.values())


def match_lines(ubl_lines: list, dodois_items: list,
                qty_tol: float = 0.01, price_tol: float = 0.02) -> list:
    """
    Match Dodois supply items to aggregated UBL lines by qty + total price.
    ubl_lines: list of dicts {description, quantity, line_total}  (already aggregated)
    dodois_items: list of dicts {rawMaterialId, containerId, qty, totalWithVat}
    Returns: list of {description, rawMaterialId, containerId}
    Ambiguous matches skipped.
    """
    results = []
    for item in dodois_items:
        candidates = [
            line for line in ubl_lines
            if abs(line["quantity"] - item["qty"]) <= qty_tol
            and abs(line["line_total"] - item["totalWithVat"]) <= price_tol
        ]
        if len(candidates) == 1:
            results.append({
                "description": candidates[0]["description"],
                "rawMaterialId": item["rawMaterialId"],
                "containerId": item.get("containerId"),
            })
        elif len(candidates) > 1:
            logger.debug("Ambiguous: rawMaterialId=%s → %d candidates", item["rawMaterialId"], len(candidates))
    return results


# ---------------------------------------------------------------------------
# DB write
# ---------------------------------------------------------------------------

def write_mappings(session, invoice, matches: list, dry_run: bool) -> dict:
    """
    Upsert ProductMapping rows from matched lines.
    invoice: Invoice ORM object with .sender_oib, .sender_name
    Returns counts: {new, skipped_existing, skipped_no_catalog}
    """
    from app.db.models import (
        DodoisRawMaterialCatalog, ProductMapping, SupplierMapping,
        get_or_create_supplier_mapping,
    )

    supplier_mapping = session.query(SupplierMapping).filter_by(
        eracun_oib=invoice.sender_oib
    ).first()
    if not supplier_mapping:
        supplier_mapping = get_or_create_supplier_mapping(
            session, invoice.sender_oib, invoice.sender_name
        )

    counts = {"new": 0, "skipped_existing": 0, "skipped_no_catalog": 0}

    for match in matches:
        raw_mat = session.query(DodoisRawMaterialCatalog).filter_by(
            dodois_material_id=match["rawMaterialId"],
            dodois_container_id=match["containerId"],
        ).first()
        if not raw_mat:
            raw_mat = session.query(DodoisRawMaterialCatalog).filter_by(
                dodois_material_id=match["rawMaterialId"],
            ).first()
        if not raw_mat:
            logger.debug("No catalog row for rawMaterialId=%s", match["rawMaterialId"])
            counts["skipped_no_catalog"] += 1
            continue

        pm = session.query(ProductMapping).filter_by(
            supplier_mapping_id=supplier_mapping.id,
            eracun_description=match["description"],
        ).first()

        if pm and pm.dodois_raw_material_id is not None:
            counts["skipped_existing"] += 1
            continue

        if not dry_run:
            if pm:
                pm.dodois_raw_material_id = raw_mat.id
                pm.enabled = True
            else:
                session.add(ProductMapping(
                    supplier_mapping_id=supplier_mapping.id,
                    eracun_description=match["description"],
                    dodois_raw_material_id=raw_mat.id,
                    enabled=True,
                ))
        counts["new"] += 1

    if not dry_run:
        session.commit()
    return counts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Match eRačun invoices with Dodois supplies")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing DB")
    parser.add_argument("--from", dest="from_date", default="2025-01-01")
    parser.add_argument("--to", dest="to_date", default=str(date.today()))
    args = parser.parse_args()

    from app.core.config_loader import load_config, get_database_url
    from app.core.dodois_auth import DodoisSession
    from app.core.dodois_client import DodoisClient
    from app.core.ubl_parser import parse_ubl_xml
    from app.db.models import Invoice, get_engine, get_session_factory

    cfg_path = os.environ.get("ERACUN_CONFIG", "config.yaml")
    cfg = load_config(cfg_path)

    xml_dir = Path(cfg.get("storage", {}).get("xml_dir", "/app/data/xmls"))
    db_url = get_database_url(cfg)
    session = get_session_factory(get_engine(db_url))()

    dodois_cfg = cfg.get("dodois", {})
    ds = DodoisSession(
        dodois_cfg["username"],
        dodois_cfg["password"],
        dodois_cfg.get("totp_secret", ""),
    )
    client = DodoisClient(ds)
    pizzerias = dodois_cfg.get("pizzerias", {})

    # Fetch all Dodois supplies for all departments
    all_supplies_raw = []
    for piz_key, piz in pizzerias.items():
        dept_id = piz.get("department_id", "")
        if not dept_id:
            logger.warning("Skipping %s — no department_id", piz_key)
            continue
        logger.info("Fetching supplies for %s (%s)…", piz_key, dept_id)
        supplies = client.get_all_supplies(dept_id, args.from_date, args.to_date)
        logger.info("  Got %d supplies", len(supplies))
        all_supplies_raw.extend(supplies)

    # Deduplicate by (invoiceNumber, supplierId)
    seen: set = set()
    supplies_deduped = []
    for s in all_supplies_raw:
        key = (s.get("invoiceNumber"), s.get("supplierId"))
        if key not in seen:
            seen.add(key)
            supplies_deduped.append(s)
    logger.info("Unique supplies: %d (raw: %d)", len(supplies_deduped), len(all_supplies_raw))

    stats = dict(
        supplies=len(supplies_deduped),
        inv_matched=0, inv_not_found=0, inv_no_xml=0,
        line_matches=0,
        map_new=0, map_existing=0, map_no_catalog=0,
    )

    for supply_summary in supplies_deduped:
        dodois_inv_num = supply_summary.get("invoiceNumber", "")
        eracun_num = dodois_to_eracun(dodois_inv_num)

        invoice = (
            session.query(Invoice)
            .filter(Invoice.invoice_number.ilike(f"%{eracun_num}%"))
            .first()
        )
        if not invoice:
            logger.debug("Not found: %s → %s", dodois_inv_num, eracun_num)
            stats["inv_not_found"] += 1
            continue
        stats["inv_matched"] += 1

        if not invoice.xml_path:
            stats["inv_no_xml"] += 1
            continue
        xml_full = xml_dir / invoice.xml_path
        if not xml_full.exists():
            logger.warning("Missing XML: %s", xml_full)
            stats["inv_no_xml"] += 1
            continue

        try:
            ubl = parse_ubl_xml(xml_full.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Parse failed %s: %s", invoice.invoice_number, exc)
            stats["inv_no_xml"] += 1
            continue

        ubl_lines_agg = aggregate_ubl_lines(ubl.lines)

        try:
            detail = client.get_supply_detail(supply_summary["id"])
        except Exception as exc:
            logger.warning("Detail fetch failed %s: %s", supply_summary.get("id"), exc)
            continue

        dodois_items = detail.get("supplyItems") or detail.get("items") or []
        if not dodois_items:
            continue

        matches = match_lines(ubl_lines_agg, dodois_items)
        stats["line_matches"] += len(matches)

        counts = write_mappings(session, invoice, matches, dry_run=args.dry_run)
        stats["map_new"] += counts["new"]
        stats["map_existing"] += counts["skipped_existing"]
        stats["map_no_catalog"] += counts["skipped_no_catalog"]

    session.close()

    dry = " [DRY RUN]" if args.dry_run else ""
    print(f"\n=== match_invoices{dry} ===")
    print(f"Dodois supplies:         {stats['supplies']}")
    print(f"Invoices matched:        {stats['inv_matched']}")
    print(f"Invoices not found:      {stats['inv_not_found']}")
    print(f"Invoices without XML:    {stats['inv_no_xml']}")
    print(f"Line matches:            {stats['line_matches']}")
    print(f"ProductMappings new:     {stats['map_new']}")
    print(f"  already set (skipped): {stats['map_existing']}")
    print(f"  no catalog (skipped):  {stats['map_no_catalog']}")


if __name__ == "__main__":
    main()
