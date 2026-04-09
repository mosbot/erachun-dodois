"""Tests for telegram_notifier — all network calls mocked."""
from datetime import datetime
from unittest.mock import MagicMock, patch

from app.core.telegram_notifier import (
    _format_caption,
    send_invoice_notification,
)


def test_format_caption_basic():
    caption = _format_caption(
        "METRO Cash & Carry",
        datetime(2026, 1, 28),
        "2315/11/6005",
        58.72,
    )
    assert "METRO Cash & Carry" in caption
    assert "28.01.2026" in caption
    assert "2315/11/6005" in caption
    assert "€58.72" in caption
    assert "Supply auto-created in Dodois — please verify." in caption


def test_format_caption_handles_none_date():
    caption = _format_caption("Foo", None, "X-1", 10.0)
    assert "—" in caption


def _ok_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"ok": True, "result": {"message_id": 1}}
    return resp


def test_send_with_pdf_calls_send_document():
    with patch("app.core.telegram_notifier.requests.post", return_value=_ok_response()) as mock_post:
        ok, err = send_invoice_notification(
            bot_token="TOKEN",
            chat_id=-100123,
            supplier="METRO",
            issue_date=datetime(2026, 1, 28),
            invoice_number="2315/11/6005",
            total_with_vat=58.72,
            pdf_bytes=b"%PDF-1.4 fake",
            pdf_filename="metro.pdf",
            topic_id=42,
        )

    assert ok is True
    assert err is None
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0].endswith("/botTOKEN/sendDocument")
    assert kwargs["data"]["chat_id"] == "-100123"
    assert kwargs["data"]["message_thread_id"] == "42"
    assert "METRO" in kwargs["data"]["caption"]
    assert "2315/11/6005" in kwargs["data"]["caption"]
    assert kwargs["files"]["document"][0] == "metro.pdf"
    assert kwargs["files"]["document"][1] == b"%PDF-1.4 fake"


def test_send_without_pdf_calls_send_message():
    with patch("app.core.telegram_notifier.requests.post", return_value=_ok_response()) as mock_post:
        ok, err = send_invoice_notification(
            bot_token="TOKEN",
            chat_id=123,
            supplier="Foo",
            issue_date=datetime(2026, 2, 1),
            invoice_number="A-1",
            total_with_vat=10.0,
            pdf_bytes=None,
        )

    assert ok is True
    args, _ = mock_post.call_args
    assert args[0].endswith("/botTOKEN/sendMessage")


def test_send_without_topic_id_omits_thread_field():
    with patch("app.core.telegram_notifier.requests.post", return_value=_ok_response()) as mock_post:
        send_invoice_notification(
            bot_token="T", chat_id=1,
            supplier="S", issue_date=None,
            invoice_number="I", total_with_vat=1.0,
            pdf_bytes=b"x",
        )
    _, kwargs = mock_post.call_args
    assert "message_thread_id" not in kwargs["data"]


def test_send_returns_error_on_http_failure():
    resp = MagicMock()
    resp.status_code = 400
    resp.text = "bad chat"
    with patch("app.core.telegram_notifier.requests.post", return_value=resp):
        ok, err = send_invoice_notification(
            bot_token="T", chat_id=1,
            supplier="S", issue_date=None,
            invoice_number="I", total_with_vat=1.0,
            pdf_bytes=b"x",
        )
    assert ok is False
    assert "400" in err


def test_send_returns_error_on_api_not_ok():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"ok": False, "description": "chat not found"}
    with patch("app.core.telegram_notifier.requests.post", return_value=resp):
        ok, err = send_invoice_notification(
            bot_token="T", chat_id=1,
            supplier="S", issue_date=None,
            invoice_number="I", total_with_vat=1.0,
            pdf_bytes=b"x",
        )
    assert ok is False
    assert "chat not found" in err


def test_send_returns_error_on_network_exception():
    import requests as _req
    with patch("app.core.telegram_notifier.requests.post",
               side_effect=_req.ConnectionError("boom")):
        ok, err = send_invoice_notification(
            bot_token="T", chat_id=1,
            supplier="S", issue_date=None,
            invoice_number="I", total_with_vat=1.0,
            pdf_bytes=b"x",
        )
    assert ok is False
    assert "boom" in err


def test_empty_bot_token_is_rejected():
    ok, err = send_invoice_notification(
        bot_token="", chat_id=1,
        supplier="S", issue_date=None,
        invoice_number="I", total_with_vat=1.0, pdf_bytes=None,
    )
    assert ok is False


def test_empty_chat_id_is_rejected():
    ok, err = send_invoice_notification(
        bot_token="T", chat_id="",
        supplier="S", issue_date=None,
        invoice_number="I", total_with_vat=1.0, pdf_bytes=None,
    )
    assert ok is False
