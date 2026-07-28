import pytest
from unittest.mock import MagicMock
import requests

from app.core.planfact_client import PlanfactClient, PlanfactError, MAX_PAGES, PAGE_SIZE


def _client(session):
    c = PlanfactClient(api_key="k", retry_delay=0)
    c.session = session
    return c


def _response(status=200, json_data=None, text="{}"):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.json.return_value = json_data if json_data is not None else {}
    return r


def test_create_outcome_returns_data():
    s = MagicMock()
    s.post.return_value = _response(json_data={"isSuccess": True,
                                               "data": {"operationId": 42}})
    assert _client(s).create_outcome({"value": 1})["data"]["operationId"] == 42


def test_create_outcome_tolerates_empty_body():
    """A 2xx with no body must not raise — Dodois taught us this the hard way."""
    s = MagicMock()
    s.post.return_value = _response(text="   ")
    assert _client(s).create_outcome({"value": 1}) == {}


def test_business_error_raises_without_retry():
    s = MagicMock()
    s.post.return_value = _response(
        json_data={"isSuccess": False, "errorMessage": "bad project",
                   "errorCode": "E1"})
    with pytest.raises(PlanfactError, match="bad project"):
        _client(s).create_outcome({"value": 1})
    assert s.post.call_count == 1


def test_client_error_is_not_retried():
    s = MagicMock()
    s.post.return_value = _response(status=400, text="bad request")
    with pytest.raises(PlanfactError):
        _client(s).create_outcome({"value": 1})
    assert s.post.call_count == 1


def test_server_error_is_retried_then_raises():
    s = MagicMock()
    s.post.return_value = _response(status=503, text="upstream down")
    with pytest.raises(PlanfactError):
        _client(s).create_outcome({"value": 1})
    assert s.post.call_count == 3


def test_list_operations_paginates():
    s = MagicMock()
    page1 = _response(json_data={"data": {"items": [{"operationId": i}
                                                    for i in range(100)]}})
    page2 = _response(json_data={"data": {"items": [{"operationId": 100}]}})
    s.post.side_effect = [page1, page2]
    ops = _client(s).list_operations(666927, "2026-07-01", "2026-07-31")
    assert len(ops) == 101
    assert s.post.call_count == 2


def test_create_outcome_accepts_204_no_content():
    """A 204 No Content (empty success) must be treated as success."""
    s = MagicMock()
    s.post.return_value = _response(status=204, text="")
    assert _client(s).create_outcome({"value": 1}) == {}


def test_list_operations_retries_5xx_then_raises():
    """5xx errors during list_operations are retried and then raise PlanfactError."""
    s = MagicMock()
    s.post.return_value = _response(status=503, text="service unavailable")
    with pytest.raises(PlanfactError):
        _client(s).list_operations(666927, "2026-07-01", "2026-07-31")
    assert s.post.call_count == 3


def test_list_operations_wraps_timeout_in_planfact_error():
    """Timeout exceptions during list_operations are caught, retried, and wrapped."""
    s = MagicMock()
    s.post.side_effect = requests.Timeout("request timed out")
    with pytest.raises(PlanfactError, match="Timeout"):
        _client(s).list_operations(666927, "2026-07-01", "2026-07-31")
    assert s.post.call_count == 3


def test_list_operations_raises_after_max_pages_instead_of_looping_forever():
    """A server that ignores paging.offset must not spin forever -- verified
    it was still issuing requests after 501 iterations before this cap
    (finding I1). It must raise rather than return a partial list, since a
    partial list looks exactly like "no existing operation"."""
    s = MagicMock()
    full_page = _response(json_data={
        "data": {"items": [{"operationId": i, "externalId": str(i)}
                           for i in range(PAGE_SIZE)]}})
    s.post.return_value = full_page
    with pytest.raises(PlanfactError, match="666927"):
        _client(s).list_operations(666927, "2026-01-01", "2026-12-31")
    assert s.post.call_count == MAX_PAGES


def test_list_operations_stops_on_empty_page():
    """An empty page (not just a short one) must also stop pagination."""
    s = MagicMock()
    full_page = _response(json_data={
        "data": {"items": [{"operationId": i} for i in range(PAGE_SIZE)]}})
    empty_page = _response(json_data={"data": {"items": []}})
    s.post.side_effect = [full_page, empty_page]
    ops = _client(s).list_operations(666927, "2026-07-01", "2026-07-31")
    assert len(ops) == PAGE_SIZE
    assert s.post.call_count == 2


def test_create_outcome_wraps_connection_error():
    """ConnectionError during create_outcome is retried and wrapped in PlanfactError."""
    s = MagicMock()
    s.post.side_effect = requests.ConnectionError("connection refused")
    with pytest.raises(PlanfactError, match="ConnectionError"):
        _client(s).create_outcome({"value": 1})
    assert s.post.call_count == 3
