"""
Seed ProductMapping for METRO based on historical Dodois supply data.
Reads metro-supplies-detail.json (scraped from Dodois) and creates
eracun_description -> DodoisRawMaterialCatalog mappings for METRO supplier.

Usage: python scripts/seed_metro_mappings.py [path_to_detail_json] [path_to_catalog_json]
"""
import json
import sys
import os
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from app.core.config_loader import load_config, get_database_url
from app.db.models import (
    get_engine, get_session_factory,
    DodoisSupplierCatalog, DodoisRawMaterialCatalog,
    SupplierMapping, ProductMapping,
)

METRO_OIB = "38016445738"
METRO_DODOIS_ID = "11eeeb8be458f06caf0d5b3908d3a4aa"


def build_combos(supplies: list, catalog: list) -> list:
    """Extract unique (rawMaterialId, containerId, matName) from supply history,
    cross-referenced with catalog for size/unit info."""
    metro_cat = next(s for s in catalog if s["supplier"]["name"] == "METRO")
    mat_lookup = {m["id"]: m for m in metro_cat["materials"]}

    seen = {}
    for supply in supplies:
        for item in supply.get("items", []):
            key = (item["rawMaterialId"], item["containerId"])
            if key in seen:
                continue
            mat = mat_lookup.get(item["rawMaterialId"])
            if not mat:
                continue
            cont = next((c for c in mat["containers"] if c["id"] == item["containerId"]), None)
            if cont is None and mat["containers"]:
                cont = mat["containers"][0]
            seen[key] = {
                "rawMaterialId": mat["id"],
                "containerId": cont["id"] if cont else None,
                "matName": mat["name"],
                "typeName": mat["typeName"],
                "unit": mat["unit"],
                "size": cont["size"] if cont else 1,
            }
    return list(seen.values())


def seed_mappings(combos: list, session) -> dict:
    metro_catalog = session.query(DodoisSupplierCatalog).filter_by(
        dodois_id=METRO_DODOIS_ID
    ).first()
    if not metro_catalog:
        print("ERROR: METRO not found in dodois_supplier_catalog. Run sync_dodois_catalog.py first.")
        return {}

    supplier_mapping = session.query(SupplierMapping).filter_by(
        eracun_oib=METRO_OIB
    ).first()
    if not supplier_mapping:
        print(f"ERROR: SupplierMapping for METRO OIB {METRO_OIB} not found.")
        return {}

    # Link supplier mapping to catalog if not already
    if not supplier_mapping.dodois_catalog_id:
        supplier_mapping.dodois_catalog_id = metro_catalog.id
        supplier_mapping.enabled = True
        session.commit()
        print(f"Linked SupplierMapping to METRO catalog (id={metro_catalog.id})")

    added = updated = skipped = 0

    for combo in combos:
        mat_name = combo["matName"].strip()
        if not mat_name:
            skipped += 1
            continue

        # Find catalog row by material_id + container_id
        raw_mat = session.query(DodoisRawMaterialCatalog).filter_by(
            supplier_catalog_id=metro_catalog.id,
            dodois_material_id=combo["rawMaterialId"],
            dodois_container_id=combo["containerId"],
        ).first()

        # Fallback: find by material_id only (any container)
        if not raw_mat:
            raw_mat = session.query(DodoisRawMaterialCatalog).filter_by(
                supplier_catalog_id=metro_catalog.id,
                dodois_material_id=combo["rawMaterialId"],
            ).first()

        if not raw_mat:
            print(f"  SKIP (no catalog row): {mat_name} | matId={combo['rawMaterialId'][:8]}..")
            skipped += 1
            continue

        # Check if ProductMapping already exists for this description
        existing = session.query(ProductMapping).filter_by(
            supplier_mapping_id=supplier_mapping.id,
            eracun_description=mat_name,
        ).first()

        if existing:
            # Update raw material link if not set
            if not existing.dodois_raw_material_id:
                existing.dodois_raw_material_id = raw_mat.id
                updated += 1
                print(f"  UPDATED: {mat_name} -> {raw_mat.dodois_name}")
            else:
                skipped += 1
        else:
            session.add(ProductMapping(
                supplier_mapping_id=supplier_mapping.id,
                eracun_description=mat_name,
                dodois_raw_material_id=raw_mat.id,
                enabled=True,
            ))
            added += 1
            print(f"  ADDED:   {mat_name:50s} -> {raw_mat.dodois_name}")

    session.commit()
    return {"added": added, "updated": updated, "skipped": skipped}


def main():
    detail_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/metro-supplies-detail.json"
    catalog_path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/dodois-catalog.json"

    with open(detail_path) as f:
        supplies = json.load(f)
    with open(catalog_path) as f:
        catalog = json.load(f)

    combos = build_combos(supplies, catalog)
    print(f"Unique combos from supply history: {len(combos)}")

    cfg_path = os.environ.get("ERACUN_CONFIG", "config.yaml")
    cfg = load_config(cfg_path)
    db_url = get_database_url(cfg)
    session = get_session_factory(get_engine(db_url))()

    result = seed_mappings(combos, session)
    session.close()

    print(f"\nDone: {result.get('added')} added, {result.get('updated')} updated, {result.get('skipped')} skipped")


if __name__ == "__main__":
    main()
