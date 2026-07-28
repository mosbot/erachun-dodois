"""
CLI entrypoint for posting Wolt/Glovo invoices to PlanFact.

Called by the host cron job via ``sync_invoices.sh``, right after
``sync_eracun.py``. Anything that fails stays unposted and is retried on the
next run — that is the whole point of running it on a schedule.

Exit codes:
    0 — run completed (any number of invoices posted, including 0)
    1 — configuration missing (PlanFact API key not set)
    2 — runtime error, including a refused --invoice re-post (see --force)

Usage:
    python scripts/post_to_planfact.py
    python scripts/post_to_planfact.py --dry-run
    python scripts/post_to_planfact.py --invoice 918
    python scripts/post_to_planfact.py --invoice 918 --force
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
from app.core.planfact_poster import (
    BLOCKED_PREFIX, post_invoice, select_candidates, should_notify,
)
from app.core.telegram_notifier import send_alert
from app.db.models import get_engine, get_session_factory, Invoice

logger = logging.getLogger("post_to_planfact")


def _alerting_configured(cfg: dict) -> bool:
    """Whether Telegram alerting has bot_token AND alerts_chat_id set.

    config.yaml ships ``alerts_chat_id: ""``, so on a server where nobody
    has finished the Telegram setup this is False — that is expected, not
    an error condition by itself. See finding I4/I6 for what depends on it.
    """
    tg = cfg.get("telegram", {}) or {}
    bot_token = (tg.get("bot_token") or "").strip()
    chat_id = tg.get("alerts_chat_id")
    return bool(bot_token and chat_id)


def _notify_failure(cfg: dict, invoice: Invoice, error: str) -> bool:
    """Send a per-invoice failure alert to Telegram. Never raises.

    Assumes the caller already checked _alerting_configured(cfg) — this
    function only reports on delivery, it does not distinguish "not
    configured" from "send failed" (see finding I4: those two cases need
    different handling by the caller, so the distinction is made before
    this is even called).

    Returns True only when the alert was actually delivered, so the caller
    (process_one) can decide whether it is safe to record this failure as
    "already reported".
    """
    tg = cfg.get("telegram", {}) or {}
    bot_token = (tg.get("bot_token") or "").strip()
    chat_id = tg.get("alerts_chat_id")

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
    return ok


def _notify_run_failure(cfg: dict, message: str) -> None:
    """Best-effort alert for a run-level (not per-invoice) failure.

    Covers finding I6: a missing API key or an unhandled exception in
    main() previously produced no Telegram message at all, so the most
    likely failure mode of the whole job — it not running at all — was the
    one nobody heard about. Must never raise: a broken alert path must not
    mask or replace the original error already being logged/returned.
    """
    if not _alerting_configured(cfg):
        return
    try:
        tg = cfg.get("telegram", {}) or {}
        ok, err = send_alert(
            (tg.get("bot_token") or "").strip(), tg.get("alerts_chat_id"),
            f"⚠️ post_to_planfact run failed\n{message}",
            topic_id=tg.get("alerts_topic_id") or None)
        if not ok:
            logger.warning("Run-failure alert could not be delivered: %s", err)
    except Exception:
        logger.exception("Unexpected error while sending run-failure alert")


def process_one(session, client, cfg: dict, invoice: Invoice, xml_dir: Path,
                dry_run: bool = False) -> str:
    """Post a single invoice and persist the outcome. Never raises.

    Returns "posted", "failed", or "would-post" (dry-run candidates that
    validated cleanly). Wrapped in its own try/except so that one bad
    invoice — a stale DB connection failing on commit, an unreadable XML
    file, anything unforeseen — cannot abort the whole batch: this job runs
    unattended every 30 minutes and must keep making progress on the
    remaining candidates.
    """
    try:
        xml_text = ""
        if invoice.xml_path and (xml_dir / invoice.xml_path).exists():
            xml_text = (xml_dir / invoice.xml_path).read_text(
                encoding="utf-8", errors="replace")

        op_id, error = post_invoice(client, cfg, invoice, xml_text,
                                    dry_run=dry_run)

        if op_id:
            if not dry_run:
                invoice.planfact_operation_id = op_id
                invoice.planfact_posted_at = datetime.utcnow()
                invoice.planfact_error = None
                session.commit()
            logger.info("POSTED  %s -> operation %s (%.2f EUR, %s)",
                        invoice.invoice_number, op_id, invoice.total_with_vat or 0.0,
                        invoice.dodois_pizzeria)
            return "posted"

        if error:
            # A "Blocked:" error is a deliberate exclusion, not a failure —
            # Wolt Drive invoices arrive twice a month and are never booked,
            # and an alert for each one only teaches the reader to ignore
            # alerts. The reason is still recorded and shown in the UI.
            blocked = error.startswith(BLOCKED_PREFIX)
            notify = (not blocked) and should_notify(invoice.planfact_error, error)
            if not dry_run:
                if notify and _alerting_configured(cfg):
                    # The persisted planfact_error means "this failure has
                    # been reported to the admin". Attempt the alert BEFORE
                    # writing anything, and commit the new error only once
                    # delivery is confirmed. If the alert fails, leave the
                    # previous value untouched so should_notify() still sees
                    # a change on the next run and retries the alert — for
                    # up to one cycle the detail panel may therefore show
                    # the previous (or no) error for an invoice whose alert
                    # hasn't gone out yet. That is deliberate: an
                    # undelivered alert must stay pending rather than be
                    # silently marked as seen.
                    if _notify_failure(cfg, invoice, error):
                        invoice.planfact_error = error
                        session.commit()
                    else:
                        logger.warning(
                            "Telegram alert for %s could not be delivered; "
                            "will retry next run", invoice.invoice_number)
                else:
                    # Either this failure was already reported (should_notify
                    # is False), or alerting simply isn't configured at all.
                    # The "hold pending until delivered" behaviour above only
                    # makes sense when a delivery attempt could still
                    # succeed later — with no bot_token/alerts_chat_id there
                    # is nothing to wait for, so persist immediately. Without
                    # this, a persisted planfact_error means "admin was
                    # told", but with alerting unconfigured it would never be
                    # written at all, and the detail panel would show
                    # "Queued" forever for an invoice that has been failing
                    # for weeks (finding I4).
                    invoice.planfact_error = error
                    session.commit()
            if blocked:
                logger.info("SKIPPED %s: %s", invoice.invoice_number, error)
            else:
                logger.error("FAILED  %s: %s", invoice.invoice_number, error)
            return "failed"

        logger.info("WOULD POST  %s (%.2f EUR, %s)",
                    invoice.invoice_number, invoice.total_with_vat or 0.0,
                    invoice.dodois_pizzeria)
        return "would-post"

    except Exception:
        logger.exception("Unexpected error processing invoice %s",
                         getattr(invoice, "invoice_number", "?"))
        session.rollback()
        return "failed"


def main() -> int:
    parser = argparse.ArgumentParser(description="Post invoices to PlanFact")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be posted, send nothing")
    parser.add_argument("--invoice", type=int, default=None,
                        help="Process a single invoice by database id")
    parser.add_argument("--force", action="store_true",
                        help="With --invoice, re-process even if it already "
                             "carries a planfact_operation_id")
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
        _notify_run_failure(
            cfg, "PlanFact API key is not configured (planfact.api_key "
                 "missing). No invoices were processed this run.")
        return 1

    if not _alerting_configured(cfg):
        logger.warning(
            "Telegram alerting not configured (bot_token/alerts_chat_id "
            "missing) — failures will be persisted and logged, but nobody "
            "will be notified")

    engine = get_engine(get_database_url(cfg))
    session = get_session_factory(engine)()
    xml_dir = Path(get_storage_config(cfg).get("xml_dir", "/app/data/xmls"))

    try:
        if args.invoice:
            inv = session.query(Invoice).get(args.invoice)
            if inv is None:
                logger.info("Invoice id %d not found — nothing to post",
                           args.invoice)
                return 0
            if inv.planfact_operation_id and not args.force:
                # This is exactly the flag an operator uses to ask "did this
                # one post?" — silently re-posting an already-posted invoice
                # would create a duplicate PlanFact can't detect on its own
                # (finding I5).
                logger.error(
                    "Invoice %s (id=%d) is already posted as PlanFact "
                    "operation %s — refusing to re-process. Pass --force "
                    "to override.",
                    inv.invoice_number, inv.id, inv.planfact_operation_id)
                return 2
            candidates = [inv]
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

        # Each invoice gets its own failure boundary inside process_one() —
        # this job runs unattended every 30 minutes, so one bad invoice must
        # never abort the batch and strand every other candidate.
        posted = skipped = failed = 0
        for inv in candidates:
            result = process_one(session, client, cfg, inv, xml_dir, args.dry_run)
            if result == "posted":
                posted += 1
            elif result == "failed":
                failed += 1
            else:
                skipped += 1

        logger.info("Done: posted=%d failed=%d would-post=%d",
                    posted, failed, skipped)
        return 0

    except Exception as exc:
        logger.error("Run failed: %s", exc, exc_info=True)
        _notify_run_failure(cfg, f"Unexpected error: {exc}")
        return 2
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
