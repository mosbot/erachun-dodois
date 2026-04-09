"""Telegram notification after a successful Dodois upload.

Best-effort: exceptions are wrapped and returned as (ok, error) — callers
decide whether to surface failures in the UI. The uploader path never
fails just because Telegram is unreachable.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


def _format_caption(
    supplier: str,
    issue_date: Optional[datetime],
    invoice_number: str,
    total_with_vat: float,
    currency: str = "EUR",
) -> str:
    """Plain-text caption. No MarkdownV2 escaping hell."""
    date_str = issue_date.strftime("%d.%m.%Y") if issue_date else "—"
    symbol = "€" if currency.upper() == "EUR" else currency
    return (
        f"📦 {supplier}\n"
        f"📅 {date_str}\n"
        f"🧾 {invoice_number}\n"
        f"💰 {symbol}{total_with_vat:,.2f}"
    )


def send_invoice_notification(
    bot_token: str,
    chat_id: int | str,
    supplier: str,
    issue_date: Optional[datetime],
    invoice_number: str,
    total_with_vat: float,
    pdf_bytes: Optional[bytes],
    pdf_filename: str = "invoice.pdf",
    topic_id: Optional[int] = None,
    currency: str = "EUR",
    timeout: float = 15.0,
) -> tuple[bool, Optional[str]]:
    """Send a Dodois-upload notification to a Telegram chat (optionally a topic).

    Returns (ok, error_message). On success error_message is None.
    If pdf_bytes is None, sends a text message instead of a document.
    """
    if not bot_token:
        return False, "bot_token is empty"
    if not chat_id:
        return False, "chat_id is empty"

    caption = _format_caption(
        supplier, issue_date, invoice_number, total_with_vat, currency,
    )

    try:
        if pdf_bytes:
            url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendDocument"
            data = {
                "chat_id": str(chat_id),
                "caption": caption,
            }
            if topic_id is not None:
                data["message_thread_id"] = str(topic_id)
            files = {
                "document": (pdf_filename, pdf_bytes, "application/pdf"),
            }
            resp = requests.post(url, data=data, files=files, timeout=timeout)
        else:
            url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
            data = {
                "chat_id": str(chat_id),
                "text": caption,
            }
            if topic_id is not None:
                data["message_thread_id"] = str(topic_id)
            resp = requests.post(url, data=data, timeout=timeout)

        if resp.status_code != 200:
            return False, f"Telegram API {resp.status_code}: {resp.text[:200]}"
        body = resp.json()
        if not body.get("ok"):
            return False, f"Telegram API error: {body.get('description', 'unknown')}"
        return True, None
    except requests.RequestException as e:
        logger.warning("Telegram notification failed: %s", e)
        return False, str(e)
    except Exception as e:  # pragma: no cover — defensive
        logger.exception("Unexpected Telegram notification error")
        return False, str(e)
