"""
Match eRačun invoices with Dodois supplies to auto-populate ProductMapping.

Usage:
    python scripts/match_invoices.py [--dry-run] [--from DATE] [--to DATE]

Options:
    --dry-run     Show what would be written without touching DB
    --from DATE   Start date (default: 2025-01-01)
    --to DATE     End date (default: today)
"""
from __future__ import annotations

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
    Handles multiple Dodois formats:
      6/0(011)0003/004488  →  4488/11/6003
      0/0(011)0003/004488  →  4488/11/0003
      0/0 (010) 0001/008344  →  8344/10/0001  (with spaces)
      0/0(011)/0002/025468  →  25468/11/0002  (extra slash)
      0/0(010)000/026543   →  26543/10/0000  (no slash before A)
    Formula: C/0(0BB)0YYY/00000A  →  A/BB/CYYY
    If unrecognised, return unchanged.
    """
    inv_number = inv_number.strip()
    # Normalize: remove spaces around parentheses and slashes
    # C/0(0BB) 0YYY/00AAAA  or  C/0(0BB)/0YYY/00AAAA  or  C/0(0BB)0YYY00AAAA
    m = re.match(
        r"^(\d)/0\s*\(0(\d+)\)\s*/?\s*0*(\d{3,4})\s*/?\s*0*(\d+)$",
        inv_number,
    )
    if m:
        c, bb, yyy, a = m.groups()
        return f"{int(a)}/{int(bb)}/{c}{yyy[-3:]}"
    return inv_number


def extract_invoice_key(inv_number: str) -> tuple[str, str] | None:
    """
    Extract (A, BB) key from eRačun format A/BB/CYYY.
    Example: 4488/11/6003 → ("4488", "11")
    These two numbers uniquely identify a METRO invoice across both systems.
    """
    m = re.match(r"^(\d+)/(\d+)/\d+$", inv_number.strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def find_dodois_match(eracun_key: tuple[str, str], dodois_supplies: list) -> dict | None:
    """
    Find a Dodois supply matching eRačun invoice key (A, BB).
    Searches through all Dodois formats:
      - C/0(0BB)0YYY/00AAAA (structured)
      - AAAA/BB/... (flat)
      - direct eRačun format
    Returns first matching supply dict or None.
    """
    a_num, bb_num = eracun_key

    for supply in dodois_supplies:
        dodois_inv = supply.get("invoiceNumber", "").strip()
        # Try structured format: extract A and BB via dodois_to_eracun
        converted = dodois_to_eracun(dodois_inv)
        if converted != dodois_inv:
            conv_key = extract_invoice_key(converted)
            if conv_key and conv_key[0] == a_num and conv_key[1] == bb_num:
                return supply
        # Try direct match: eRačun number appears as-is
        direct_key = extract_invoice_key(dodois_inv)
        if direct_key and direct_key[0] == a_num and direct_key[1] == bb_num:
            return supply
    return None


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
                price_tol: float = 0.02) -> list:
    """
    Match Dodois supply items to aggregated UBL lines by totalPriceWithoutVat ≈ line_total.

    UBL line_total is price without VAT. Dodois totalPriceWithoutVat is the same.
    Quantity units differ (UBL: kg/pcs, Dodois: grams/pcs) so we match on price only.
    Ambiguous matches (multiple lines with same total) are skipped.

    ubl_lines: list of dicts {description, quantity, line_total}
    dodois_items: list of dicts from Dodois API (quantity, totalPriceWithVat, totalPriceWithoutVat, ...)
    Returns: list of {description, rawMaterialId, containerId}
    """
    results = []
    for item in dodois_items:
        item_total = (
            item.get("totalPriceWithoutVat")
            or item.get("totalWithVat")  # fallback for tests with old keys
            or 0
        )
        candidates = [
            line for line in ubl_lines
            if abs(line["line_total"] - item_total) <= price_tol
        ]
        if len(candidates) == 1:
            results.append({
                "description": candidates[0]["description"],
                "rawMaterialId": item["rawMaterialId"],
                "containerId": item.get("rawMaterialContainerId") or item.get("containerId"),
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
    parser.add_argument("--supplier", default="METRO",
                        help="Dodois supplier name to filter (default: METRO)")
    parser.add_argument("--oib", default="38016445738",
                        help="eRačun sender OIB to filter (default: METRO 38016445738)")
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
    db_session = get_session_factory(get_engine(db_url))()

    dodois_cfg = cfg.get("dodois", {})
    ds = DodoisSession(
        dodois_cfg["username"],
        dodois_cfg["password"],
        dodois_cfg.get("totp_secret", ""),
    )
    client = DodoisClient(ds)
    pizzerias = dodois_cfg.get("pizzerias", {})

    # Step 1: Get eRačun invoices for this supplier
    eracun_invoices = (
        db_session.query(Invoice)
        .filter_by(sender_oib=args.oib)
        .all()
    )
    logger.info("eRačun invoices for OIB %s: %d", args.oib, len(eracun_invoices))

    # Step 2: Fetch all Dodois supplies, filter by supplier name
    all_supplies = []
    for piz_key, piz in pizzerias.items():
        dept_id = piz.get("department_id", "")
        if not dept_id:
            continue
        logger.info("Fetching Dodois supplies for %s…", piz_key)
        supplies = client.get_all_supplies(dept_id, args.from_date, args.to_date)
        logger.info("  Got %d supplies", len(supplies))
        all_supplies.extend(supplies)

    dodois_supplies = [
        s for s in all_supplies
        if s.get("supplierName", "").upper() == args.supplier.upper()
    ]
    logger.info("Dodois %s supplies: %d", args.supplier, len(dodois_supplies))

    stats = dict(
        eracun_total=len(eracun_invoices),
        dodois_total=len(dodois_supplies),
        pairs_found=0, pairs_not_found=0, no_xml=0,
        line_matches=0, line_ambiguous=0,
        map_new=0, map_existing=0, map_no_catalog=0,
    )

    # Step 3: For each eRačun invoice, find Dodois pair by invoice key
    for invoice in eracun_invoices:
        eracun_key = extract_invoice_key(invoice.invoice_number)
        if not eracun_key:
            logger.warning("Cannot parse eRačun number: %s", invoice.invoice_number)
            stats["pairs_not_found"] += 1
            continue

        dodois_match = find_dodois_match(eracun_key, dodois_supplies)
        if not dodois_match:
            logger.info("No Dodois pair for %s", invoice.invoice_number)
            stats["pairs_not_found"] += 1
            continue

        stats["pairs_found"] += 1
        logger.info(
            "PAIR: %s ↔ %s (Dodois: %s)",
            invoice.invoice_number,
            dodois_match.get("invoiceNumber"),
            dodois_match.get("id", "")[:12],
        )

        # Parse eRačun XML
        if not invoice.xml_path:
            stats["no_xml"] += 1
            continue
        xml_full = xml_dir / invoice.xml_path
        if not xml_full.exists():
            logger.warning("Missing XML: %s", xml_full)
            stats["no_xml"] += 1
            continue

        try:
            ubl = parse_ubl_xml(xml_full.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Parse failed %s: %s", invoice.invoice_number, exc)
            stats["no_xml"] += 1
            continue

        ubl_lines_agg = aggregate_ubl_lines(ubl.lines)

        # Fetch Dodois supply detail
        try:
            detail = client.get_supply_detail(dodois_match["id"])
        except Exception as exc:
            logger.warning("Detail fetch failed: %s", exc)
            continue

        dodois_items = detail.get("supplyItems") or detail.get("items") or []
        if not dodois_items:
            continue

        # Match lines by price
        matches = match_lines(ubl_lines_agg, dodois_items)
        stats["line_matches"] += len(matches)
        stats["line_ambiguous"] += len(dodois_items) - len(matches)

        if matches:
            logger.info("  Matched %d/%d lines", len(matches), len(dodois_items))
            for m in matches:
                logger.info("    %s → %s", m["description"][:40], m["rawMaterialId"][:20])

        # Write mappings
        counts = write_mappings(db_session, invoice, matches, dry_run=args.dry_run)
        stats["map_new"] += counts["new"]
        stats["map_existing"] += counts["skipped_existing"]
        stats["map_no_catalog"] += counts["skipped_no_catalog"]

    db_session.close()

    dry = " [DRY RUN]" if args.dry_run else ""
    print(f"\n=== match_invoices — {args.supplier}{dry} ===")
    print(f"eRačun invoices:         {stats['eracun_total']}")
    print(f"Dodois supplies:         {stats['dodois_total']}")
    print(f"Pairs found:             {stats['pairs_found']}")
    print(f"Pairs not found:         {stats['pairs_not_found']}")
    print(f"No XML:                  {stats['no_xml']}")
    print(f"Line matches:            {stats['line_matches']}")
    print(f"Line unmatched:          {stats['line_ambiguous']}")
    print(f"ProductMappings new:     {stats['map_new']}")
    print(f"  already set (skipped): {stats['map_existing']}")
    print(f"  no catalog (skipped):  {stats['map_no_catalog']}")


if __name__ == "__main__":
    main()
