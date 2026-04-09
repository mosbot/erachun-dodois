"""
Fetch Dodois supplier + raw material catalog via API and save as JSON
compatible with sync_dodois_catalog.py.

Usage: python scripts/fetch_dodois_catalog.py [output_path]
Default output: /tmp/dodois-catalog.json
"""
import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    output_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dodois-catalog.json"

    from app.core.config_loader import load_config
    from app.core.dodois_auth import DodoisSession
    from app.core.dodois_client import DodoisClient

    cfg_path = os.environ.get("ERACUN_CONFIG", "config.yaml")
    cfg = load_config(cfg_path)
    dodois_cfg = cfg.get("dodois", {})

    ds = DodoisSession(
        dodois_cfg["username"],
        dodois_cfg["password"],
        dodois_cfg.get("totp_secret", ""),
    )
    client = DodoisClient(ds)

    logger.info("Fetching suppliers...")
    suppliers = client.get_suppliers()
    logger.info("Got %d suppliers", len(suppliers))

    catalog = []
    for i, sup in enumerate(suppliers):
        sup_id = sup.get("id", "")
        sup_name = sup.get("name", "")
        logger.info("[%d/%d] %s — fetching materials...", i + 1, len(suppliers), sup_name)

        try:
            raw = client.get_raw_materials(sup_id)
        except Exception as exc:
            logger.warning("  Failed: %s", exc)
            raw = []

        # Transform API response to sync_dodois_catalog.py format
        # API returns: {id, name, materialType: {unitOfMeasure, name}, containers: [{id, size}]}
        materials = []
        for item in raw:
            mat_type = item.get("materialType") or {}
            mat = {
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "typeName": mat_type.get("name") or item.get("name", ""),
                "unit": mat_type.get("unitOfMeasure", 1),
                "containers": [],
            }
            containers = item.get("containers") or []
            for c in containers:
                mat["containers"].append({
                    "id": c.get("id", ""),
                    "size": c.get("size", 1.0),
                })
            materials.append(mat)

        catalog.append({
            "supplier": {
                "id": sup_id,
                "name": sup_name,
                "inn": sup.get("inn", ""),
            },
            "materials": materials,
        })
        logger.info("  %d materials", len(materials))

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    total_mats = sum(len(e["materials"]) for e in catalog)
    logger.info("Saved %d suppliers, %d materials to %s", len(catalog), total_mats, output_path)


if __name__ == "__main__":
    main()
