"""
Tests for CLEANUP/UNFILLED exclusion in _today_realized_pnl().

Three locations were fixed: orchestrator.py, agents/risk.py,
agents/alpaca_broker.py. Each must exclude positions closed for
CLEANUP or UNFILLED reasons so phantom P&L doesn't distort the
daily loss limit and bonus target checks.
"""
from datetime import date
from unittest.mock import patch

TODAY = date.today().isoformat()


def _pos(pnl: float, reason: str = "TARGET") -> dict:
    return {
        "status": "CLOSED",
        "realized_pnl": pnl,
        "closed_at": f"{TODAY}T14:00:00",
        "close_reason": reason,
    }


# ── orchestrator._today_realized_pnl ──────────────────────────────────────────

class TestOrchestratorRealizedPnl:

    def _call(self, rows):
        with patch("core.db.select", return_value=rows):
            import importlib, orchestrator
            importlib.reload(orchestrator)
            from orchestrator import _today_realized_pnl
            return _today_realized_pnl()

    def test_target_position_counted(self):
        assert self._call([_pos(200.0, "TARGET")]) == 200.0

    def test_stop_position_counted(self):
        assert self._call([_pos(-100.0, "STOP")]) == -100.0

    def test_cleanup_excluded(self):
        assert self._call([_pos(999.0, "CLEANUP")]) == 0.0

    def test_unfilled_excluded(self):
        assert self._call([_pos(999.0, "UNFILLED")]) == 0.0

    def test_mix_only_real_trades_sum(self):
        rows = [
            _pos(300.0, "TARGET"),
            _pos(-100.0, "STOP"),
            _pos(999.0, "CLEANUP"),
            _pos(999.0, "UNFILLED"),
        ]
        assert self._call(rows) == 200.0


# ── agents/risk._today_realized_pnl ───────────────────────────────────────────

class TestRiskRealizedPnl:

    def _call(self, rows):
        with patch("agents.risk.db.select", return_value=rows):
            from agents.risk import _today_realized_pnl
            return _today_realized_pnl()

    def test_target_position_counted(self):
        assert self._call([_pos(200.0, "TARGET")]) == 200.0

    def test_cleanup_excluded(self):
        assert self._call([_pos(999.0, "CLEANUP")]) == 0.0

    def test_unfilled_excluded(self):
        assert self._call([_pos(999.0, "UNFILLED")]) == 0.0

    def test_mix_only_real_trades_sum(self):
        rows = [
            _pos(300.0, "TARGET"),
            _pos(-100.0, "STOP"),
            _pos(50.0, "CLEANUP"),
            _pos(50.0, "UNFILLED"),
        ]
        assert self._call(rows) == 200.0


# ── agents/alpaca_broker today_realized (inside update_positions_intraday) ────

class TestAlpacaBrokerRealizedPnl:
    """
    Verify CLEANUP/UNFILLED exclusion in update_positions_intraday().
    The today_realized sum is used to check DAILY_BONUS_TARGET. A CLEANUP
    position with phantom P&L must not cause the bonus-target close to fire.
    """

    def _open_pos(self) -> dict:
        return {
            "id": "p1", "ticker": "AAPL",
            "entry_price": 180.0, "target_price": 187.2,
            "stop_loss": 176.5, "shares": 16,
            "high_watermark": 180.0, "low_watermark": 180.0,
        }

    def _run(self, closed_rows):
        from unittest.mock import MagicMock
        from config.settings import DAILY_BONUS_TARGET

        open_pos = [self._open_pos()]
        current_price = 181.0  # below target, above stop — no normal close

        def fake_select(table, **kw):
            if kw.get("filters", {}).get("status") == "CLOSED":
                return closed_rows
            return []

        mock_close = MagicMock()

        with patch("agents.alpaca_broker._reconcile_with_alpaca"), \
             patch("agents.alpaca_broker.open_positions", return_value=open_pos), \
             patch("agents.alpaca_broker.db.select", side_effect=fake_select), \
             patch("agents.alpaca_broker.db.update"), \
             patch("agents.alpaca_broker.get_current_price", return_value=current_price), \
             patch("agents.alpaca_broker.get_intraday_signals", return_value={}), \
             patch("agents.alpaca_broker._close_position", mock_close):
            from agents.alpaca_broker import update_positions_intraday
            update_positions_intraday()

        return mock_close

    def test_cleanup_does_not_trigger_bonus_target(self):
        """Huge CLEANUP P&L must not fire the bonus-target close on open positions."""
        closed_rows = [_pos(9999.0, "CLEANUP")]
        mock_close = self._run(closed_rows)
        bonus_calls = [c for c in mock_close.call_args_list
                       if "BONUS_TARGET" in str(c)]
        assert bonus_calls == [], "CLEANUP P&L should not trigger BONUS_TARGET close"

    def test_unfilled_does_not_trigger_bonus_target(self):
        """Huge UNFILLED P&L must not fire the bonus-target close on open positions."""
        closed_rows = [_pos(9999.0, "UNFILLED")]
        mock_close = self._run(closed_rows)
        bonus_calls = [c for c in mock_close.call_args_list
                       if "BONUS_TARGET" in str(c)]
        assert bonus_calls == [], "UNFILLED P&L should not trigger BONUS_TARGET close"
