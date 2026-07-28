"""
CLI entrypoint for posting Wolt/Glovo invoices to PlanFact.

Called by the host cron job via ``sync_invoices.sh``, right after
``sync_eracun.py``. Anything that fails stays unposted and is retried on the
next run — that is the whole point of running it on a schedule.

Exit codes:
    0 — run completed (any number of invoices posted, including 0)
    1 — configuration missing (PlanFact API key not set)
    2 — runtime error

Usage:
    python scripts/post_to_planfact.py
    python scripts/post_to_planfact.py --dry-run
    python scripts/post_to_planfact.py --invoice 918
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from app.core.config_loader import load_config, get_database_url, get_storage_config
from app.core.planfact_client import PlanfactClient
from app.core.planfact_poster import post_invoice, select_candidates, should_notify
from app.core.telegram_notifier import send_alert
from app.db.models import get_engine, get_session_factory, Invoice

logger = logging.getLogger("post_to_planfact")


def _notify_failure(cfg: dict, invoice: Invoice, error: str) -> None:
    """Report a new failure to Telegram. Never raises — this is best-effort."""
    tg = cfg.get("telegram", {}) or {}
    bot_token = (tg.get("bot_token") or "").strip()
    chat_id = tg.get("alerts_chat_id")
    if not bot_token or not chat_id:
        logger.warning("Telegram alerts not configured — failure not notified")
        return

    date = invoice.vat_date or invoice.issue_date
    text = (
        f"❌ PlanFact posting failed\n"
        f"{invoice.sender_name}\n"
        f"Invoice {invoice.invoice_number}"
        f"{' · ' + date.strftime('%d.%m.%Y') if date else ''}\n"
        f"{(invoice.total_with_vat or 0.0):,.2f} EUR"
        f"{' · ' + invoice.dodois_pizzeria if invoice.dodois_pizzeria else ''}\n"
        f"\n{error}"
    )
    ok, err = send_alert(bot_token, chat_id, text,
                         topic_id=tg.get("alerts_topic_id") or None)
    if not ok:
        logger.warning("Telegram alert failed: %s", err)


def main() -> int:
    parser = argparse.ArgumentParser(description="Post invoices to PlanFact")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be posted, send nothing")
    parser.add_argument("--invoice", type=int, default=None,
                        help="Process a single invoice by database id")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config()
    pf = cfg.get("planfact", {}) or {}
    if not pf.get("enabled", False):
        logger.info("PlanFact integration disabled (planfact.enabled=false)")
        return 0
    if not pf.get("api_key"):
        logger.error("PlanFact not configured (planfact.api_key missing)")
        return 1

    engine = get_engine(get_database_url(cfg))
    session = get_session_factory(engine)()
    xml_dir = Path(get_storage_config(cfg).get("xml_dir", "/app/data/xmls"))

    try:
        if args.invoice:
            candidates = [session.query(Invoice).get(args.invoice)]
            candidates = [c for c in candidates if c]
        else:
            candidates = select_candidates(session, cfg)

        if not candidates:
            logger.info("Nothing to post")
            return 0

        logger.info("%d invoice(s) to consider%s",
                    len(candidates), " (dry run)" if args.dry_run else "")

        client = PlanfactClient(
            pf["api_key"],
            base_url=pf.get("base_url", "https://api.planfact.io/api/v1"))

        posted = skipped = failed = 0
        for inv in candidates:
            xml_text = ""
            if inv.xml_path and (xml_dir / inv.xml_path).exists():
                xml_text = (xml_dir / inv.xml_path).read_text(
                    encoding="utf-8", errors="replace")

            op_id, error = post_invoice(client, cfg, inv, xml_text,
                                        dry_run=args.dry_run)

            if op_id:
                if not args.dry_run:
                    inv.planfact_operation_id = op_id
                    inv.planfact_posted_at = datetime.utcnow()
                    inv.planfact_error = None
                    session.commit()
                posted += 1
                logger.info("POSTED  %s -> operation %s (%.2f EUR, %s)",
                            inv.invoice_number, op_id, inv.total_with_vat or 0.0,
                            inv.dodois_pizzeria)
            elif error:
                notify = should_notify(inv.planfact_error, error)
                if not args.dry_run:
                    inv.planfact_error = error
                    session.commit()
                    if notify:
                        _notify_failure(cfg, inv, error)
                failed += 1
                logger.error("FAILED  %s: %s", inv.invoice_number, error)
            else:
                skipped += 1
                logger.info("WOULD POST  %s (%.2f EUR, %s)",
                            inv.invoice_number, inv.total_with_vat or 0.0,
                            inv.dodois_pizzeria)

        logger.info("Done: posted=%d failed=%d would-post=%d",
                    posted, failed, skipped)
        return 0

    except Exception as exc:
        logger.error("Run failed: %s", exc, exc_info=True)
        return 2
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
