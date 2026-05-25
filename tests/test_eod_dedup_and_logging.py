"""
Tests for Gap 5 (EOD dedup) and Gap 6 (run observability + alerts) in
trading-agent-b/orchestrator.py and core/alerts.py.
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import date

TODAY = date.today().isoformat()

_SCORE_RESULT = {"scored": 5, "promoted": 1, "demoted": 0}


# ── Gap 5: EOD dedup ──────────────────────────────────────────────────────────

class TestEODDedup:

    def test_eod_skips_when_already_ran(self):
        """EOD bails early when run_eod_started record exists for today."""
        with patch("orchestrator.db") as mock_db, \
             patch("orchestrator._is_halted", return_value=False):
            def _sel(table, **kw):
                f = kw.get("filters", {})
                if table == "b_scan_results" and f.get("scan_type") == "run_eod_started":
                    return [{"id": 1, "date": TODAY}]
                return []
            mock_db.select.side_effect = _sel
            from orchestrator import eod
            eod(broker="simulation")
            # _log_run_b (insert) must NOT have been called
            mock_db.insert.assert_not_called()

    def test_eod_proceeds_when_no_prior_run(self):
        """EOD runs and logs run_eod_started when no prior run exists."""
        with patch("orchestrator.db") as mock_db, \
             patch("orchestrator._is_halted", return_value=False), \
             patch("orchestrator.open_positions", return_value=[]), \
             patch("orchestrator.close_all_positions", return_value=[]), \
             patch("orchestrator.score_today", return_value=_SCORE_RESULT), \
             patch("orchestrator.write_daily_performance"):
            mock_db.select.return_value = []
            mock_db.insert.return_value = {}
            from orchestrator import eod
            eod(broker="simulation")
            assert mock_db.insert.called
            scan_types = [
                c[0][1].get("scan_type", "") for c in mock_db.insert.call_args_list
                if len(c[0]) >= 2 and isinstance(c[0][1], dict)
            ]
            assert any("run_eod_started" in s for s in scan_types)

    def test_eod_dedup_query_exception_proceeds(self):
        """EOD proceeds gracefully if dedup query raises (table may not exist yet)."""
        with patch("orchestrator.db") as mock_db, \
             patch("orchestrator._is_halted", return_value=False), \
             patch("orchestrator.open_positions", return_value=[]), \
             patch("orchestrator.close_all_positions", return_value=[]), \
             patch("orchestrator.score_today", return_value=_SCORE_RESULT), \
             patch("orchestrator.write_daily_performance"):
            def _sel(table, **kw):
                f = kw.get("filters", {})
                if table == "b_scan_results" and f.get("scan_type") == "run_eod_started":
                    raise Exception("Table not found")
                return []
            mock_db.select.side_effect = _sel
            mock_db.insert.return_value = {}
            from orchestrator import eod
            eod(broker="simulation")   # must not raise


# ── Gap 6: Run logging ────────────────────────────────────────────────────────

class TestRunLogging:

    def test_log_run_b_inserts_correct_fields(self):
        """_log_run_b writes a b_scan_results record with correct scan_type and results."""
        with patch("orchestrator.db") as mock_db:
            mock_db.insert.return_value = {}
            from orchestrator import _log_run_b
            _log_run_b("eod", "started", {"detail": "x"})
            mock_db.insert.assert_called_once()
            table, payload = mock_db.insert.call_args[0]
            assert table == "b_scan_results"
            assert payload["scan_type"] == "run_eod_started"
            assert payload["results"]["mode"] == "eod"
            assert payload["results"]["status"] == "started"
            assert payload["results"]["detail"] == "x"

    def test_log_run_b_swallows_db_error(self):
        """_log_run_b doesn't raise when db.insert fails — observability is best-effort."""
        with patch("orchestrator.db") as mock_db:
            mock_db.insert.side_effect = Exception("DB down")
            from orchestrator import _log_run_b
            _log_run_b("eod", "failed")   # must not raise

    def test_eod_logs_completed_on_success(self):
        """EOD inserts run_eod_completed record after successful run."""
        with patch("orchestrator.db") as mock_db, \
             patch("orchestrator._is_halted", return_value=False), \
             patch("orchestrator.open_positions", return_value=[]), \
             patch("orchestrator.close_all_positions", return_value=[]), \
             patch("orchestrator.score_today", return_value=_SCORE_RESULT), \
             patch("orchestrator.write_daily_performance"):
            mock_db.select.return_value = []
            mock_db.insert.return_value = {}
            from orchestrator import eod
            eod(broker="simulation")
            scan_types = [
                c[0][1].get("scan_type", "") for c in mock_db.insert.call_args_list
                if len(c[0]) >= 2 and isinstance(c[0][1], dict)
            ]
            assert any("run_eod_completed" in s for s in scan_types)

    def test_eod_logs_failed_and_reraises_on_exception(self):
        """EOD inserts run_eod_failed and re-raises when an unexpected error occurs."""
        with patch("orchestrator.db") as mock_db, \
             patch("orchestrator._is_halted", return_value=False), \
             patch("orchestrator.open_positions", side_effect=RuntimeError("boom")), \
             patch("orchestrator.send_alert"):
            mock_db.select.return_value = []
            mock_db.insert.return_value = {}
            from orchestrator import eod
            with pytest.raises(RuntimeError, match="boom"):
                eod(broker="alpaca")
            scan_types = [
                c[0][1].get("scan_type", "") for c in mock_db.insert.call_args_list
                if len(c[0]) >= 2 and isinstance(c[0][1], dict)
            ]
            assert any("run_eod_failed" in s for s in scan_types)


# ── Gap 6: EOD alerts ─────────────────────────────────────────────────────────

class TestEODAlerts:

    def test_alert_sent_when_alpaca_positions_not_closed(self):
        """Alert fires when position was open before EOD and still open after close attempt."""
        open_pos = [{"ticker": "AAPL", "id": 1}]
        # First db.select = dedup guard (no prior run); second = open_after check (still open)
        with patch("orchestrator.db") as mock_db, \
             patch("orchestrator._is_halted", return_value=False), \
             patch("orchestrator.open_positions", return_value=open_pos), \
             patch("orchestrator.close_all_positions", return_value=[]), \
             patch("orchestrator.score_today", return_value=_SCORE_RESULT), \
             patch("orchestrator.write_daily_performance"), \
             patch("orchestrator.send_alert") as mock_alert:
            mock_db.select.side_effect = [[], open_pos]
            mock_db.insert.return_value = {}
            from orchestrator import eod
            eod(broker="alpaca")
            mock_alert.assert_called_once()
            subject = mock_alert.call_args[0][0]
            assert "FAILED" in subject or "still open" in subject.lower()

    def test_no_alert_when_positions_closed_successfully(self):
        """No alert is sent when positions were closed successfully."""
        open_pos = [{"ticker": "AAPL", "id": 1}]
        with patch("orchestrator.db") as mock_db, \
             patch("orchestrator._is_halted", return_value=False), \
             patch("orchestrator.open_positions", return_value=open_pos), \
             patch("orchestrator.close_all_positions", return_value=[{"ticker": "AAPL"}]), \
             patch("orchestrator.score_today", return_value=_SCORE_RESULT), \
             patch("orchestrator.write_daily_performance"), \
             patch("orchestrator.send_alert") as mock_alert:
            mock_db.select.return_value = []
            mock_db.insert.return_value = {}
            from orchestrator import eod
            eod(broker="alpaca")
            mock_alert.assert_not_called()

    def test_alert_sent_on_eod_crash(self):
        """send_alert is called when the EOD run crashes with an unexpected exception."""
        with patch("orchestrator.db") as mock_db, \
             patch("orchestrator._is_halted", return_value=False), \
             patch("orchestrator.open_positions", side_effect=ValueError("unexpected")), \
             patch("orchestrator.send_alert") as mock_alert:
            mock_db.select.return_value = []
            mock_db.insert.return_value = {}
            from orchestrator import eod
            with pytest.raises(ValueError):
                eod(broker="alpaca")
            mock_alert.assert_called_once()
            assert "FAILED" in mock_alert.call_args[0][0]
