"""
Tests for core/alerts.py — dispatch logic.

Verifies: Telegram tried first, Gmail fallback, ledger logging when both fail.
"""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────

def _run_send_alert(telegram_ok: bool, gmail_ok: bool, monkeypatch=None, tmp_path=None):
    """Run send_alert with both channel outcomes controlled."""
    from core import alerts

    with patch("core.ntfy.send_alert", return_value=telegram_ok) as mock_tg, \
         patch("core.alerts._try_gmail", return_value=gmail_ok) as mock_gm:
        alerts.send_alert("Test subject", "Test body")

    return mock_tg, mock_gm


# ── Channel priority ───────────────────────────────────────────────

class TestChannelPriority:
    def test_telegram_tried_first(self):
        mock_tg, mock_gm = _run_send_alert(telegram_ok=True, gmail_ok=False)
        mock_tg.assert_called_once_with("Test subject", "Test body")

    def test_gmail_not_tried_when_telegram_succeeds(self):
        _, mock_gm = _run_send_alert(telegram_ok=True, gmail_ok=False)
        mock_gm.assert_not_called()

    def test_gmail_tried_when_telegram_fails(self):
        _, mock_gm = _run_send_alert(telegram_ok=False, gmail_ok=True)
        mock_gm.assert_called_once_with("Test subject", "Test body")


# ── Ledger on total failure ────────────────────────────────────────

class TestLedgerFallback:
    def test_logs_alert_delivery_failed_when_both_channels_fail(self, monkeypatch, tmp_path):
        import core.ledger as ledger_mod
        monkeypatch.setattr(ledger_mod, "_DATA_DIR", tmp_path)
        from core import alerts
        with patch("core.ntfy.send_alert", return_value=False), \
             patch("core.alerts._try_gmail", return_value=False):
            alerts.send_alert("Missed alert", "body")

        events = ledger_mod.read_today()
        assert any(e["event"] == "alert_delivery_failed" for e in events)
        fail_ev = next(e for e in events if e["event"] == "alert_delivery_failed")
        assert fail_ev["data"]["subject"] == "Missed alert"

    def test_no_ledger_write_when_telegram_succeeds(self, monkeypatch, tmp_path):
        import core.ledger as ledger_mod
        monkeypatch.setattr(ledger_mod, "_DATA_DIR", tmp_path)
        from core import alerts
        with patch("core.ntfy.send_alert", return_value=True):
            alerts.send_alert("Delivered", "body")

        assert ledger_mod.read_today() == []

    def test_no_ledger_write_when_gmail_fallback_succeeds(self, monkeypatch, tmp_path):
        import core.ledger as ledger_mod
        monkeypatch.setattr(ledger_mod, "_DATA_DIR", tmp_path)
        from core import alerts
        with patch("core.ntfy.send_alert", return_value=False), \
             patch("core.alerts._try_gmail", return_value=True):
            alerts.send_alert("Delivered via email", "body")

        assert ledger_mod.read_today() == []


# ── Gmail helper ───────────────────────────────────────────────────

class TestGmailHelper:
    def test_returns_false_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("GMAIL_USER",         raising=False)
        monkeypatch.delenv("GMAIL_APP_PASSWORD",  raising=False)
        from core.alerts import _try_gmail
        assert _try_gmail("Test", "body") is False

    def test_returns_false_on_smtp_exception(self, monkeypatch):
        monkeypatch.setenv("GMAIL_USER",         "user@gmail.com")
        monkeypatch.setenv("GMAIL_APP_PASSWORD",  "app-pass")
        with patch("smtplib.SMTP_SSL", side_effect=Exception("SMTP error")):
            from core.alerts import _try_gmail
            assert _try_gmail("Test", "body") is False
