from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pytest

import health_check as hc


@pytest.fixture(autouse=True)
def no_alerts(monkeypatch):
    monkeypatch.setattr("health_check.send_alert", MagicMock())
    yield monkeypatch.setattr  # let tests access it if needed


# ── check_eod_ran ──────────────────────────────────────────────────────────────

class TestCheckEodRan:
    def test_returns_true_when_eod_logged(self, mock_db):
        mock_db.select.return_value = [{"date": "2026-05-29", "scan_type": "run_eod_started"}]
        assert hc.check_eod_ran("2026-05-29") is True

    def test_returns_false_and_alerts_when_missing(self, mock_db):
        mock_db.select.return_value = []
        with patch("health_check.send_alert") as mock_alert:
            result = hc.check_eod_ran("2026-05-30")
        assert result is False
        mock_alert.assert_called_once()
        subject = mock_alert.call_args[0][0]
        assert "MISSED" in subject
        assert "2026-05-30" in subject

    def test_returns_false_on_db_error(self, mock_db):
        mock_db.select.side_effect = Exception("db down")
        assert hc.check_eod_ran("2026-05-29") is False


# ── check_stale_positions ──────────────────────────────────────────────────────

class TestCheckStalePositions:
    def test_returns_true_when_no_stale(self, mock_db):
        today = date.today().isoformat()
        mock_db.select.return_value = [{"ticker": "AAPL", "opened_at": f"{today}T14:00:00", "status": "OPEN"}]
        assert hc.check_stale_positions() is True

    def test_returns_true_when_no_open_positions(self, mock_db):
        mock_db.select.return_value = []
        assert hc.check_stale_positions() is True

    def test_returns_false_and_alerts_on_stale(self, mock_db):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        mock_db.select.return_value = [{"ticker": "V", "opened_at": f"{yesterday}T18:00:00", "status": "OPEN"}]
        with patch("health_check.send_alert") as mock_alert:
            result = hc.check_stale_positions()
        assert result is False
        mock_alert.assert_called_once()
        assert "V" in mock_alert.call_args[0][1]

    def test_stale_alert_names_all_tickers(self, mock_db):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        mock_db.select.return_value = [
            {"ticker": "V",    "opened_at": f"{yesterday}T18:00:00", "status": "OPEN"},
            {"ticker": "ORCL", "opened_at": f"{yesterday}T18:30:00", "status": "OPEN"},
        ]
        with patch("health_check.send_alert") as mock_alert:
            hc.check_stale_positions()
        body = mock_alert.call_args[0][1]
        assert "V" in body and "ORCL" in body


# ── _last_trading_day ──────────────────────────────────────────────────────────

class TestLastTradingDay:
    def test_monday_returns_friday(self):
        monday = date(2026, 6, 1)   # Monday
        assert hc._last_trading_day(monday) == date(2026, 5, 29)  # Friday

    def test_tuesday_returns_monday(self):
        tuesday = date(2026, 6, 2)
        assert hc._last_trading_day(tuesday) == date(2026, 6, 1)

    def test_saturday_returns_friday(self):
        saturday = date(2026, 5, 30)
        assert hc._last_trading_day(saturday) == date(2026, 5, 29)
