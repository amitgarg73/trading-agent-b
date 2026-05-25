"""
Tests for RFN-9 + RFN-10 in Strategy B.

RFN-9: premarket() checks _is_halted before dedup (not after)
RFN-10: eod() skips on non-trading days
"""
import pytest
from unittest.mock import patch, MagicMock


_SCORE_RESULT = {"scored": 0, "promoted": 0, "demoted": 0}


# ──────────────────────────────────────────────────────────────────────────────
# RFN-9: halt check precedes dedup in premarket
# ──────────────────────────────────────────────────────────────────────────────

class TestPremarketHaltBeforeDedup:
    """RFN-9: _is_halted must be checked before the dedup guard in premarket()."""

    def test_halted_system_skips_even_when_no_prior_plan(self):
        """Halted system must not proceed even if no plan exists for today."""
        with patch("orchestrator._is_trading_day", return_value=True), \
             patch("orchestrator._is_halted", return_value=True), \
             patch("orchestrator.db") as mock_db, \
             patch("orchestrator.get_pool3_with_context") as mock_pool3:
            mock_db.select.return_value = []  # no prior plan
            from orchestrator import premarket
            premarket(broker="alpaca")

        mock_pool3.assert_not_called()

    def test_halted_system_returns_before_dedup_db_read(self):
        """When halted, dedup db.select should NOT be called (halt exits first)."""
        dedup_checked = []

        def track_select(table, filters=None):
            if table == "b_trade_plans":
                dedup_checked.append(True)
            return []

        with patch("orchestrator._is_trading_day", return_value=True), \
             patch("orchestrator._is_halted", return_value=True), \
             patch("orchestrator.db") as mock_db:
            mock_db.select.side_effect = track_select
            from orchestrator import premarket
            premarket(broker="alpaca")

        assert not dedup_checked, (
            "RFN-9: dedup db.select for b_trade_plans must not run when system is halted"
        )

    def test_non_halted_dedup_still_works(self):
        """Non-halted system with prior plan today should still skip via dedup."""
        with patch("orchestrator._is_trading_day", return_value=True), \
             patch("orchestrator._is_halted", return_value=False), \
             patch("orchestrator.db") as mock_db, \
             patch("orchestrator.get_pool3_with_context") as mock_pool3:
            mock_db.select.return_value = [{"id": "plan1", "date": "2026-05-24"}]
            from orchestrator import premarket
            premarket(broker="alpaca")

        mock_pool3.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# RFN-10 (Strategy B): eod() skips on non-trading days
# ──────────────────────────────────────────────────────────────────────────────

class TestEodTradingDayGuardB:
    """RFN-10: B's eod() must exit early on non-trading days."""

    def test_eod_skips_on_non_trading_day(self):
        """eod() returns without writing dedup record on non-trading days."""
        with patch("orchestrator._is_trading_day", return_value=False), \
             patch("orchestrator._is_halted") as mock_halt, \
             patch("orchestrator.db") as mock_db:
            mock_db.select.return_value = []
            mock_db.insert.return_value = {}
            from orchestrator import eod
            eod(broker="alpaca")

        mock_halt.assert_not_called()
        mock_db.insert.assert_not_called(), "No DB writes on non-trading day"

    def test_eod_proceeds_on_trading_day(self):
        """eod() passes the trading day check and reaches the halt check."""
        with patch("orchestrator._is_trading_day", return_value=True), \
             patch("orchestrator._is_halted", return_value=True) as mock_halt, \
             patch("orchestrator.db") as mock_db:
            mock_db.select.return_value = []
            from orchestrator import eod
            eod(broker="alpaca")

        mock_halt.assert_called_once()

    def test_eod_non_trading_day_does_not_consume_dedup_slot(self):
        """Non-trading day exit must NOT write run_eod_started."""
        inserted_scan_types = []

        def capture_insert(table, payload):
            inserted_scan_types.append(payload.get("scan_type", ""))
            return {"id": "fake"}

        with patch("orchestrator._is_trading_day", return_value=False), \
             patch("orchestrator.db") as mock_db:
            mock_db.select.return_value = []
            mock_db.insert.side_effect = capture_insert
            from orchestrator import eod
            eod(broker="alpaca")

        assert "run_eod_started" not in inserted_scan_types, (
            "RFN-10: non-trading day must not write dedup record"
        )
