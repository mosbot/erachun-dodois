"""
CLI entrypoint for syncing invoices from moj-eRačun.

Designed to be called by the host cron job via ``sync_invoices.sh``. Mirrors
the manual "Sync from eRačun" button in the Streamlit UI
(`app/web/app.py::sync_invoices`) but without any UI dependency.

Exit codes:
    0 — sync completed successfully (any number of new invoices, including 0)
    1 — configuration missing (e.g. eRačun credentials not set)
    2 — runtime error during sync

Usage:
    python scripts/sync_eracun.py
    python scripts/sync_eracun.py --lookback-days 7
"""
import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from app.core.config_loader import load_config, get_database_url
from app.core.eracun_client import EracunClient, EracunCredentials
from app.core.invoice_sync import InvoiceSyncService
from app.db.models import get_engine, get_session_factory


logger = logging.getLogger("sync_eracun")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync invoices from moj-eRačun")
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="How many days to look back (overrides config.yaml sync.lookback_days)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config()
    eracun_cfg = cfg.get("eracun", {}) or {}

    if not eracun_cfg.get("username"):
        logger.error("eRačun not configured (eracun.username missing)")
        return 1

    lookback_days = args.lookback_days
    if lookback_days is None:
        lookback_days = (cfg.get("sync", {}) or {}).get("lookback_days", 90)

    creds = EracunCredentials(
        username=eracun_cfg["username"],
        password=eracun_cfg["password"],
        company_id=eracun_cfg["company_id"],
        software_id=eracun_cfg["software_id"],
        company_bu=eracun_cfg.get("company_bu", ""),
    )

    storage = cfg.get("storage", {}) or {}
    engine = get_engine(get_database_url(cfg))
    session_factory = get_session_factory(engine)

    client = EracunClient(eracun_cfg["base_url"], creds)
    try:
        service = InvoiceSyncService(
            eracun_client=client,
            session_factory=session_factory,
            pdf_dir=storage.get("pdf_dir", "/app/data/pdfs"),
            xml_dir=storage.get("xml_dir", "/app/data/xmls"),
        )
        logger.info("Starting sync (lookback_days=%d)", lookback_days)
        result = service.sync(lookback_days=lookback_days)
    except Exception:
        logger.exception("Sync crashed")
        return 2
    finally:
        client.close()

    status = result.get("status")
    found = result.get("found", 0)
    new = result.get("new", 0)
    if status == "success":
        logger.info("Sync OK: %d new / %d found", new, found)
        return 0
    logger.error("Sync failed: %s", result.get("error", "unknown"))
    return 2


if __name__ == "__main__":
    sys.exit(main())
