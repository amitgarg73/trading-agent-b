"""
Tests for core/ntfy.py.

Covers: configured/unconfigured env, successful send, HTTP failure,
network exception, ledger logging on failure, never raises, request format.
"""
import pytest
from unittest.mock import patch, MagicMock


# ── Helpers ───────────────────────────────────────────────────────

def _with_topic(monkeypatch, topic="test-trading-abc123"):
    monkeypatch.setenv("NTFY_TOPIC", topic)


def _mock_http_success():
    resp = MagicMock()
    resp.status = 200
    resp.__enter__ = lambda s: s
    resp.__exit__  = MagicMock(return_value=False)
    return resp


def _mock_http_failure(status=400):
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__  = MagicMock(return_value=False)
    return resp


# ── Unconfigured ──────────────────────────────────────────────────

class TestUnconfigured:
    def test_returns_false_when_no_topic(self, monkeypatch):
        monkeypatch.delenv("NTFY_TOPIC", raising=False)
        from core import ntfy
        assert ntfy.send_alert("Test", "body") is False


# ── Successful send ───────────────────────────────────────────────

class TestSuccess:
    def test_returns_true_on_200(self, monkeypatch):
        _with_topic(monkeypatch)
        from core import ntfy
        with patch("urllib.request.urlopen", return_value=_mock_http_success()):
            result = ntfy.send_alert("Subject", "Body text")
        assert result is True

    def test_request_url_contains_topic(self, monkeypatch):
        _with_topic(monkeypatch, topic="my-trading-topic")
        from core import ntfy
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            return _mock_http_success()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ntfy.send_alert("Test", "body")

        assert "my-trading-topic" in captured["url"]

    def test_subject_sent_as_title_header(self, monkeypatch):
        _with_topic(monkeypatch)
        from core import ntfy
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["headers"] = dict(req.headers)
            return _mock_http_success()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ntfy.send_alert("My Alert Subject", "body")

        assert captured["headers"].get("Title") == "My Alert Subject"

    def test_body_sent_as_request_data(self, monkeypatch):
        _with_topic(monkeypatch)
        from core import ntfy
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = req.data.decode()
            return _mock_http_success()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ntfy.send_alert("Subject", "Alert body content")

        assert captured["body"] == "Alert body content"


# ── Failure paths ──────────────────────────────────────────────────

class TestFailure:
    def test_returns_false_on_non_200(self, monkeypatch):
        _with_topic(monkeypatch)
        from core import ntfy
        with patch("urllib.request.urlopen", return_value=_mock_http_failure(400)):
            assert ntfy.send_alert("Fail", "body") is False

    def test_returns_false_on_network_exception(self, monkeypatch):
        _with_topic(monkeypatch)
        from core import ntfy
        with patch("urllib.request.urlopen", side_effect=OSError("network error")):
            assert ntfy.send_alert("Fail", "body") is False

    def test_logs_to_ledger_on_exception(self, monkeypatch, tmp_path):
        _with_topic(monkeypatch)
        import core.ledger as ledger_mod
        monkeypatch.setattr(ledger_mod, "_DATA_DIR", tmp_path)
        from core import ntfy
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            ntfy.send_alert("Fail", "body")

        events = ledger_mod.read_today()
        assert any(e["event"] == "alert_delivery_failed" for e in events)
        fail_ev = next(e for e in events if e["event"] == "alert_delivery_failed")
        assert fail_ev["data"]["channel"] == "ntfy"

    def test_never_raises(self, monkeypatch):
        _with_topic(monkeypatch)
        from core import ntfy
        with patch("urllib.request.urlopen", side_effect=Exception("unexpected")):
            ntfy.send_alert("Crash test", "body")  # must not propagate
