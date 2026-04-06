"""
Sync Dodois supplier + raw material catalog from scraped JSON into the DB.
Usage: python scripts/sync_dodois_catalog.py [path_to_json]

The JSON is produced by the browser JS scraper in app.py (Mappings > Suppliers > Sync).
Default JSON path: /tmp/dodois-catalog.json
"""
import json
import sys
import os
from pathlib import Path

# Add project root to path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from app.core.config_loader import load_config, get_database_url
from app.db.models import (
    init_db, get_engine, get_session_factory,
    DodoisSupplierCatalog, DodoisRawMaterialCatalog,
)
from datetime import datetime


def sync_catalog(data: list, session) -> dict:
    """
    Upsert suppliers and raw materials from scraped data.
    Returns summary counts.
    """
    now = datetime.utcnow()
    sup_added = sup_updated = mat_added = mat_updated = 0

    for entry in data:
        sup = entry["supplier"]
        materials = entry.get("materials", [])

        # Upsert supplier catalog
        catalog = session.query(DodoisSupplierCatalog).filter_by(dodois_id=sup["id"]).first()
        if catalog:
            catalog.dodois_name = sup["name"]
            if sup.get("inn"):
                catalog.dodois_inn = sup["inn"]
            catalog.synced_at = now
            sup_updated += 1
        else:
            catalog = DodoisSupplierCatalog(
                dodois_id=sup["id"],
                dodois_name=sup["name"],
                dodois_inn=sup.get("inn"),
                synced_at=now,
            )
            session.add(catalog)
            session.flush()  # get catalog.id
            sup_added += 1

        # Upsert raw materials
        for mat in materials:
            unit = mat.get("unit", 1)
            type_name = mat.get("typeName") or mat["name"]
            containers = mat.get("containers", [])

            if not containers:
                # No container (pcs without packaging)
                existing = session.query(DodoisRawMaterialCatalog).filter_by(
                    supplier_catalog_id=catalog.id,
                    dodois_material_id=mat["id"],
                    dodois_container_id=None,
                ).first()
                if existing:
                    existing.dodois_name = type_name
                    existing.unit = unit
                    existing.container_size = 1.0
                    existing.synced_at = now
                    mat_updated += 1
                else:
                    session.add(DodoisRawMaterialCatalog(
                        supplier_catalog_id=catalog.id,
                        dodois_material_id=mat["id"],
                        dodois_container_id=None,
                        dodois_name=type_name,
                        unit=unit,
                        container_size=1.0,
                        synced_at=now,
                    ))
                    mat_added += 1
            else:
                for container in containers:
                    size = container.get("size") or 1.0
                    display_name = f"{type_name} ({_size_label(size, unit)})"

                    existing = session.query(DodoisRawMaterialCatalog).filter_by(
                        supplier_catalog_id=catalog.id,
                        dodois_material_id=mat["id"],
                        dodois_container_id=container["id"],
                    ).first()
                    if existing:
                        existing.dodois_name = display_name
                        existing.unit = unit
                        existing.container_size = float(size)
                        existing.synced_at = now
                        mat_updated += 1
                    else:
                        session.add(DodoisRawMaterialCatalog(
                            supplier_catalog_id=catalog.id,
                            dodois_material_id=mat["id"],
                            dodois_container_id=container["id"],
                            dodois_name=display_name,
                            unit=unit,
                            container_size=float(size),
                            synced_at=now,
                        ))
                        mat_added += 1

    session.commit()
    return {
        "suppliers_added": sup_added,
        "suppliers_updated": sup_updated,
        "materials_added": mat_added,
        "materials_updated": mat_updated,
    }


def _size_label(size: float, unit: int) -> str:
    if unit == 5:
        return f"{int(size)}g" if size < 1000 else f"{size/1000:g}kg"
    elif unit == 8:
        return f"{size}m"
    else:
        return f"{int(size)}pcs"


def main():
    json_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dodois-catalog.json"

    print(f"Loading data from {json_path}...")
    with open(json_path) as f:
        data = json.load(f)
    print(f"  {len(data)} suppliers loaded")

    cfg_path = os.environ.get("ERACUN_CONFIG", "config.yaml")
    cfg = load_config(cfg_path)
    db_url = get_database_url(cfg)

    engine = get_engine(db_url)
    session_factory = get_session_factory(engine)
    session = session_factory()

    print("Syncing to DB...")
    result = sync_catalog(data, session)
    session.close()

    print("Done:")
    print(f"  Suppliers: {result['suppliers_added']} added, {result['suppliers_updated']} updated")
    print(f"  Materials: {result['materials_added']} added, {result['materials_updated']} updated")


if __name__ == "__main__":
    main()
