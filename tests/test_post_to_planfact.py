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
import sys
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


# ---------------------------------------------------------------------------
# I4: alerting-unconfigured must not leave a failure permanently pending
# ---------------------------------------------------------------------------

NO_TELEGRAM_CFG = {"telegram": {"bot_token": "", "alerts_chat_id": ""}}


def test_alerting_configured_true_when_both_present(module):
    assert module._alerting_configured(TELEGRAM_CFG) is True


def test_alerting_configured_false_when_chat_id_blank(module):
    """config.yaml ships alerts_chat_id: '' -- this is the shipped default,
    not a typo, and must read as 'not configured'."""
    assert module._alerting_configured(NO_TELEGRAM_CFG) is False


def test_alerting_configured_false_when_no_telegram_key_at_all(module):
    assert module._alerting_configured({}) is False


def test_process_one_persists_error_immediately_when_alerting_unconfigured(module):
    """Without this, a persisted planfact_error means 'the admin has been
    told', so with alerting unconfigured the error would never be written
    and the detail panel would show 'Queued' forever (finding I4)."""
    session = MagicMock()
    client = MagicMock()
    invoice = _invoice(planfact_error=None)

    with patch.object(module, "post_invoice", return_value=(None, "boom")), \
         patch.object(module, "send_alert") as alert:
        result = module.process_one(session, client, NO_TELEGRAM_CFG, invoice,
                                    Path("/tmp"), dry_run=False)

    assert result == "failed"
    alert.assert_not_called()
    assert invoice.planfact_error == "boom"
    session.commit.assert_called_once()


def test_process_one_still_holds_pending_on_genuine_delivery_failure(module):
    """Contrast with the previous test: when alerting IS configured but
    delivery genuinely fails, the pending-retry behaviour from the earlier
    fix round must be unchanged."""
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


# ---------------------------------------------------------------------------
# I5: --invoice must refuse to re-process an already-posted invoice
# ---------------------------------------------------------------------------

def _main_cfg(**planfact_kw):
    pf = {"enabled": True, "api_key": "k"}
    pf.update(planfact_kw)
    return {"planfact": pf, "telegram": {"bot_token": "", "alerts_chat_id": ""}}


def test_main_refuses_to_reprocess_already_posted_invoice_without_force(module):
    posted_invoice = _invoice(planfact_operation_id="op-1")
    fake_session = MagicMock()
    fake_session.query.return_value.get.return_value = posted_invoice

    with patch.object(module, "load_config", return_value=_main_cfg()), \
         patch.object(sys, "argv", ["post_to_planfact.py", "--invoice", "42"]), \
         patch.object(module, "get_engine", return_value=object()), \
         patch.object(module, "get_session_factory",
                      return_value=lambda: fake_session), \
         patch.object(module, "process_one") as process_one:
        rc = module.main()

    assert rc == 2
    process_one.assert_not_called()
    fake_session.close.assert_called_once()


def test_main_reprocesses_already_posted_invoice_with_force(module):
    posted_invoice = _invoice(planfact_operation_id="op-1")
    fake_session = MagicMock()
    fake_session.query.return_value.get.return_value = posted_invoice

    with patch.object(module, "load_config", return_value=_main_cfg()), \
         patch.object(sys, "argv",
                      ["post_to_planfact.py", "--invoice", "42", "--force"]), \
         patch.object(module, "get_engine", return_value=object()), \
         patch.object(module, "get_session_factory",
                      return_value=lambda: fake_session), \
         patch.object(module, "process_one", return_value="posted") as process_one:
        rc = module.main()

    assert rc == 0
    process_one.assert_called_once()


def test_main_invoice_not_found_is_a_clean_noop(module):
    """Unchanged behaviour: an id that doesn't exist is not an error."""
    fake_session = MagicMock()
    fake_session.query.return_value.get.return_value = None

    with patch.object(module, "load_config", return_value=_main_cfg()), \
         patch.object(sys, "argv", ["post_to_planfact.py", "--invoice", "999"]), \
         patch.object(module, "get_engine", return_value=object()), \
         patch.object(module, "get_session_factory",
                      return_value=lambda: fake_session), \
         patch.object(module, "process_one") as process_one:
        rc = module.main()

    assert rc == 0
    process_one.assert_not_called()


# ---------------------------------------------------------------------------
# I6: run-level failures must alert too, when alerting is configured
# ---------------------------------------------------------------------------

def test_notify_run_failure_sends_when_configured(module):
    with patch.object(module, "send_alert", return_value=(True, None)) as alert:
        module._notify_run_failure(TELEGRAM_CFG, "boom")
    alert.assert_called_once()


def test_notify_run_failure_noop_when_not_configured(module):
    with patch.object(module, "send_alert") as alert:
        module._notify_run_failure(NO_TELEGRAM_CFG, "boom")
    alert.assert_not_called()


def test_notify_run_failure_never_raises_on_send_exception(module):
    """Best-effort: a broken alert path must not mask the original error."""
    with patch.object(module, "send_alert", side_effect=RuntimeError("boom")):
        module._notify_run_failure(TELEGRAM_CFG, "boom")  # must not raise


def test_main_notifies_on_missing_api_key(module):
    cfg = {"planfact": {"enabled": True, "api_key": ""}, **TELEGRAM_CFG}
    with patch.object(module, "load_config", return_value=cfg), \
         patch.object(sys, "argv", ["post_to_planfact.py"]), \
         patch.object(module, "_notify_run_failure") as notify:
        rc = module.main()

    assert rc == 1
    notify.assert_called_once()


def test_main_does_not_notify_on_missing_api_key_when_alerting_unconfigured(module):
    """_notify_run_failure() itself no-ops when unconfigured; this pins that
    main() calls it unconditionally on this branch and relies on that."""
    cfg = {"planfact": {"enabled": True, "api_key": ""}, **NO_TELEGRAM_CFG}
    with patch.object(module, "load_config", return_value=cfg), \
         patch.object(sys, "argv", ["post_to_planfact.py"]), \
         patch.object(module, "send_alert") as alert:
        rc = module.main()

    assert rc == 1
    alert.assert_not_called()


def test_main_notifies_on_unexpected_runtime_exception(module):
    """finding I6's other branch: select_candidates raising (e.g. DB down)
    must alert too, not just log."""
    fake_session = MagicMock()

    with patch.object(module, "load_config", return_value=_main_cfg()), \
         patch.object(sys, "argv", ["post_to_planfact.py"]), \
         patch.object(module, "get_engine", return_value=object()), \
         patch.object(module, "get_session_factory",
                      return_value=lambda: fake_session), \
         patch.object(module, "select_candidates",
                      side_effect=RuntimeError("db down")), \
         patch.object(module, "_notify_run_failure") as notify:
        rc = module.main()

    assert rc == 2
    notify.assert_called_once()
    fake_session.close.assert_called_once()
