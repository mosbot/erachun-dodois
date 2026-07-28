"""Tests for scripts/reconcile_planfact.py.

``scripts/`` is not a package, so the module is loaded directly from its
file path with importlib, the same pattern used for
tests/test_post_to_planfact.py.

These tests pin the fix-round ordering (findings C1 and I2):

  1. Fetching operations from PlanFact must happen BEFORE any schema change
     (DDL) or invoice marking. A failed fetch must leave the database
     completely untouched and exit non-zero with an explicit message.
  2. Columns must be addable even when no API key is configured, so an
     operator without the key yet can still unbreak the portal
     (UndefinedColumn on every query once the new columns are mapped).
     The reconcile itself (matching against PlanFact) is skipped in that
     case.
"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.planfact_client import PlanfactError

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "reconcile_planfact.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("reconcile_planfact", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module():
    return _load_module()


CFG_WITH_KEY = {
    "planfact": {
        "api_key": "k",
        "accounts": {"wolt": 666927, "glovo": 666928},
        "providers": {
            "wolt": {"oib": "25531986377"},
            "glovo": {"oib": "48879371584"},
        },
    },
}

CFG_NO_KEY = {"planfact": {"accounts": {"wolt": 666927}}}


# ---------------------------------------------------------------------------
# fetch_posted_operations() — pure function, no ORM/DDL involved
# ---------------------------------------------------------------------------

def test_fetch_posted_operations_aggregates_across_accounts(module):
    client = MagicMock()
    client.list_operations.side_effect = [
        [{"operationId": 1, "externalId": "a"}],
        [{"operationId": 2, "externalId": "b"}],
    ]
    posted = module.fetch_posted_operations(
        client, {"wolt": 666927, "glovo": 666928})
    assert posted == {("wolt", "a"): "1", ("glovo", "b"): "2"}


def test_fetch_posted_operations_propagates_client_errors(module):
    """The function itself does not swallow errors -- callers (main()) are
    responsible for treating any exception as fatal and stopping before any
    schema change."""
    client = MagicMock()
    client.list_operations.side_effect = PlanfactError("boom")
    with pytest.raises(PlanfactError):
        module.fetch_posted_operations(client, {"wolt": 666927})


# ---------------------------------------------------------------------------
# main() — ordering and exit codes
# ---------------------------------------------------------------------------

def test_main_without_api_key_still_ensures_columns_but_skips_reconcile(module):
    engine_sentinel = object()
    with patch.object(module, "load_config", return_value=CFG_NO_KEY), \
         patch.object(module, "get_engine", return_value=engine_sentinel), \
         patch.object(module, "ensure_columns", return_value=True) as ensure, \
         patch.object(module, "PlanfactClient") as client_cls:
        rc = module.main(True)

    assert rc == 1
    ensure.assert_called_once_with(engine_sentinel, True)
    client_cls.assert_not_called()


def test_main_fetch_failure_skips_ddl_entirely_and_exits_nonzero(module):
    """If listing operations fails, ensure_columns() must never be called --
    the columns must not exist with nothing marked, or the very next cron
    tick re-posts every historical invoice as a duplicate."""
    with patch.object(module, "load_config", return_value=CFG_WITH_KEY), \
         patch.object(module, "get_engine", return_value=object()), \
         patch.object(module, "ensure_columns") as ensure, \
         patch.object(module, "fetch_posted_operations",
                      side_effect=PlanfactError("network down")):
        rc = module.main(True)

    assert rc == 2
    ensure.assert_not_called()


def test_main_ensures_columns_only_after_successful_fetch(module):
    """Positive case: verify the actual call order, not just that both
    happen -- fetch first, DDL second."""
    calls = []
    engine_sentinel = object()

    def fake_ensure(engine, apply_changes):
        calls.append("ensure_columns")
        return True

    def fake_fetch(client, accounts):
        calls.append("fetch_posted_operations")
        return {("wolt", "inv-1"): "op-1"}

    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.filter.return_value \
        .order_by.return_value.all.return_value = []

    with patch.object(module, "load_config", return_value=CFG_WITH_KEY), \
         patch.object(module, "get_engine", return_value=engine_sentinel), \
         patch.object(module, "ensure_columns", side_effect=fake_ensure), \
         patch.object(module, "fetch_posted_operations", side_effect=fake_fetch), \
         patch.object(module, "get_session_factory",
                      return_value=lambda: fake_session), \
         patch.object(module, "PlanfactClient", return_value=MagicMock()):
        rc = module.main(True)

    assert rc == 0
    assert calls == ["fetch_posted_operations", "ensure_columns"]


def test_main_dry_run_with_missing_columns_after_successful_fetch(module):
    """Unchanged behaviour: a dry-run that finds columns missing still
    reports rc=1 and does not write anything, but only after the fetch."""
    with patch.object(module, "load_config", return_value=CFG_WITH_KEY), \
         patch.object(module, "get_engine", return_value=object()), \
         patch.object(module, "ensure_columns", return_value=False) as ensure, \
         patch.object(module, "fetch_posted_operations",
                      return_value={("wolt", "inv-1"): "op-1"}) as fetch:
        rc = module.main(False)

    assert rc == 1
    fetch.assert_called_once()
    ensure.assert_called_once()


def test_main_empty_fetch_result_skips_ddl_and_exits_nonzero(module):
    """If PlanFact returns no operations when ~126 are expected, treat it as
    a failed reconcile. The columns must not be added with nothing marked,
    or the very next cron tick re-posts every historical invoice as a
    duplicate."""
    with patch.object(module, "load_config", return_value=CFG_WITH_KEY), \
         patch.object(module, "get_engine", return_value=object()), \
         patch.object(module, "ensure_columns") as ensure, \
         patch.object(module, "fetch_posted_operations", return_value={}):
        rc = module.main(True)

    assert rc == 2
    ensure.assert_not_called()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
