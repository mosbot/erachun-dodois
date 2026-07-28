"""Tests for process_one() in scripts/post_to_planfact.py.

``scripts/`` is not a package, so the module is loaded directly from its
file path with importlib rather than a normal import. process_one() is the
per-invoice worker extracted from main()'s loop; these tests pin two fix-
round behaviours:

  1. an unexpected exception (e.g. a stale DB connection failing on
     session.commit()) is caught inside process_one() and reported as a
     failure instead of propagating out and aborting the whole batch.
  2. a failure is only recorded as planfact_error once the Telegram alert
     for it has actually been delivered — an undelivered alert must stay
     pending (so should_notify() keeps retrying it) rather than be
     silently marked as seen.
"""
import importlib.util
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.db.models import Invoice

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "post_to_planfact.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("post_to_planfact", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module():
    return _load_module()


def _invoice(**kw):
    base = dict(
        document_nr="90247-2553198637711-2026",
        invoice_number="90247-2553198637711-2026",
        sender_oib="25531986377", sender_name="Wolt Zagreb d.o.o.",
        document_type_id=1, dodois_pizzeria="Zagreb-1",
        issue_date=datetime(2026, 7, 10), vat_date=datetime(2026, 7, 10),
        total_without_vat=636.91, total_vat=159.23, total_with_vat=796.14,
        processing_status="parsed", planfact_error=None,
    )
    base.update(kw)
    return Invoice(**base)


TELEGRAM_CFG = {"telegram": {"bot_token": "tok", "alerts_chat_id": 123,
                             "alerts_topic_id": ""}}


# ---------------------------------------------------------------------------
# Finding 1: per-invoice exception isolation
# ---------------------------------------------------------------------------

def test_process_one_isolates_a_commit_failure(module):
    """A DB hiccup on one invoice must not propagate out of process_one()."""
    session = MagicMock()
    session.commit.side_effect = RuntimeError("stale connection")
    client = MagicMock()
    invoice = _invoice()

    with patch.object(module, "post_invoice", return_value=("op-1", None)):
        result = module.process_one(session, client, {}, invoice, Path("/tmp"),
                                    dry_run=False)

    assert result == "failed"
    session.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# Finding 2: persisted error means "alert delivered"
# ---------------------------------------------------------------------------

def test_process_one_does_not_persist_error_when_alert_delivery_fails(module):
    session = MagicMock()
    client = MagicMock()
    invoice = _invoice(planfact_error=None)

    with patch.object(module, "post_invoice", return_value=(None, "boom")), \
         patch.object(module, "send_alert",
                      return_value=(False, "network down")) as alert:
        result = module.process_one(session, client, TELEGRAM_CFG, invoice,
                                    Path("/tmp"), dry_run=False)

    assert result == "failed"
    alert.assert_called_once()
    session.commit.assert_not_called()
    assert invoice.planfact_error is None
    # Because the value on the invoice was not updated, the next run's
    # should_notify() call still sees a "new" failure and retries the alert.
    assert module.should_notify(invoice.planfact_error, "boom") is True


def test_process_one_persists_error_when_alert_delivery_succeeds(module):
    session = MagicMock()
    client = MagicMock()
    invoice = _invoice(planfact_error=None)

    with patch.object(module, "post_invoice", return_value=(None, "boom")), \
         patch.object(module, "send_alert", return_value=(True, None)) as alert:
        result = module.process_one(session, client, TELEGRAM_CFG, invoice,
                                    Path("/tmp"), dry_run=False)

    assert result == "failed"
    alert.assert_called_once()
    assert invoice.planfact_error == "boom"
    session.commit.assert_called_once()


def test_process_one_repeated_identical_failure_persists_without_alerting(module):
    """should_notify() is False here, so the error is persisted directly and
    no Telegram call is made at all."""
    session = MagicMock()
    client = MagicMock()
    invoice = _invoice(planfact_error="boom")

    with patch.object(module, "post_invoice", return_value=(None, "boom")), \
         patch.object(module, "send_alert") as alert:
        result = module.process_one(session, client, TELEGRAM_CFG, invoice,
                                    Path("/tmp"), dry_run=False)

    assert result == "failed"
    alert.assert_not_called()
    assert invoice.planfact_error == "boom"
    session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Dry-run must not change: nothing written, nothing sent
# ---------------------------------------------------------------------------

def test_process_one_dry_run_writes_nothing_and_alerts_nobody(module):
    session = MagicMock()
    client = MagicMock()
    invoice = _invoice(planfact_error=None)

    with patch.object(module, "post_invoice", return_value=(None, "boom")), \
         patch.object(module, "send_alert") as alert:
        result = module.process_one(session, client, TELEGRAM_CFG, invoice,
                                    Path("/tmp"), dry_run=True)

    assert result == "failed"
    alert.assert_not_called()
    session.commit.assert_not_called()
    assert invoice.planfact_error is None


# ---------------------------------------------------------------------------
# Basic sanity coverage for the refactor's other two branches
# ---------------------------------------------------------------------------

def test_process_one_returns_posted_and_stores_operation_id(module):
    session = MagicMock()
    client = MagicMock()
    invoice = _invoice()

    with patch.object(module, "post_invoice", return_value=("op-99", None)):
        result = module.process_one(session, client, {}, invoice, Path("/tmp"),
                                    dry_run=False)

    assert result == "posted"
    assert invoice.planfact_operation_id == "op-99"
    assert invoice.planfact_error is None
    session.commit.assert_called_once()


def test_process_one_dry_run_valid_invoice_returns_would_post(module):
    session = MagicMock()
    client = MagicMock()
    invoice = _invoice()

    with patch.object(module, "post_invoice", return_value=(None, None)):
        result = module.process_one(session, client, {}, invoice, Path("/tmp"),
                                    dry_run=True)

    assert result == "would-post"
    session.commit.assert_not_called()
