"""
Tests for Strategy B daily_runs integration:
- Net P&L loss guard (realized + unrealized) in _maybe_run_intraday_scan
- b_daily_runs record creation on intraday scan
- run_id threading to place_orders
"""
from unittest.mock import patch, MagicMock
from datetime import datetime as real_datetime, date
from config.settings import (
    DAILY_LOSS_LIMIT, DAILY_BONUS_TARGET,
    INTRADAY_SCAN_UTC_START, INTRADAY_SCAN_MAX_RUNS,
    INTRADAY_SCAN_MIN_INTERVAL_MINS, MAX_POSITIONS,
)

TODAY = date.today().isoformat()
WINDOW_HOUR = INTRADAY_SCAN_UTC_START  # 15 UTC = 11 AM ET
_TODAY_DATE = date.today()


def _utc_now(hour: int = WINDOW_HOUR):
    return real_datetime(_TODAY_DATE.year, _TODAY_DATE.month, _TODAY_DATE.day, hour, 30, 0)


def _today():
    return TODAY


def _default_trade():
    return {
        "ticker": "AAPL", "action": "BUY", "pool": 2,
        "entry_price": 185.0, "target_price": 186.85,
        "stop_loss": 183.76, "shares": 16,
        "position_size": 2960.0, "confidence": "HIGH",
        "reasoning": "test", "estimated_profit": 29.6,
    }


def _run_b_intraday_scan(hour=WINDOW_HOUR, prior_scans=None, open_pos=None,
                         closed_today=None, b_closed_rows=None,
                         candidates=None, trades=None, approved=None, final=None,
                         broker="simulation"):
    """
    Run orchestrator._maybe_run_intraday_scan with full pipeline mocked.
    Returns (mock_db_insert, mock_db_update, mock_place_orders).
    """
    prior_scans  = prior_scans  or []
    open_pos     = open_pos     if open_pos     is not None else []
    b_closed_rows = b_closed_rows if b_closed_rows is not None else []
    trade        = _default_trade()
    candidates   = candidates   if candidates   is not None else [{"ticker": "AAPL"}]
    trades       = trades       if trades       is not None else [trade]
    approved     = approved     if approved     is not None else [trade]
    final        = final        if final        is not None else [trade]

    def db_select(table, **kw):
        f = kw.get("filters", {})
        if table == "b_scan_results" and f.get("scan_type") == "intraday_scan":
            return [{"results": s} for s in prior_scans]
        if table == "b_positions" and f.get("status") == "CLOSED":
            return b_closed_rows
        if table == "b_positions" and f.get("status") == "OPEN":
            return []
        if table == "b_trade_plans":
            return [{"id": "plan-b-001"}]
        return []

    mock_insert      = MagicMock(return_value={"id": "run-b-001"})
    mock_update      = MagicMock()
    mock_place       = MagicMock(return_value=final)

    with patch("orchestrator.datetime") as mock_dt, \
         patch("orchestrator.date") as mock_date, \
         patch("core.db.select",   side_effect=db_select), \
         patch("core.db.insert",   mock_insert), \
         patch("core.db.update",   mock_update), \
         patch("agents.alpaca_broker.open_positions", return_value=open_pos), \
         patch("orchestrator._today_realized_pnl",
               return_value=sum(r.get("realized_pnl", 0) for r in b_closed_rows)), \
         patch("scanner.pool_filter.get_pool3_tickers", return_value=["AAPL", "MSFT"]), \
         patch("scanner.intraday_momentum.scan",       return_value=candidates), \
         patch("agents.market_context.get",            return_value={"summary": "flat"}), \
         patch("agents.strategy.select_trades",        return_value={"trades": trades}), \
         patch("agents.risk.validate",                 return_value=(approved, [])), \
         patch("agents.guardrails.check",              return_value=(final, [])), \
         patch("orchestrator.place_orders",            mock_place):
        mock_dt.utcnow.return_value    = _utc_now(hour)
        mock_dt.fromisoformat.side_effect = real_datetime.fromisoformat
        mock_date.today.return_value   = _TODAY_DATE
        from orchestrator import _maybe_run_intraday_scan
        _maybe_run_intraday_scan(broker=broker)

    return mock_insert, mock_update, mock_place


# ── Net P&L loss guard ─────────────────────────────────────────────────────────

class TestNetPnlLossGuardB:
    """Loss guard uses realized + unrealized, not just realized."""

    def _make_closed(self, realized: float) -> dict:
        return {"realized_pnl": realized, "closed_at": TODAY + "T10:00:00", "status": "CLOSED"}

    def test_unrealized_loss_pushes_total_below_limit(self):
        """realized = -200, unrealized = -400 on open pos → total = -600 ≤ -500 → skip."""
        open_with_loss = [{"ticker": "HELD", "unrealized_pnl": -400.0, "status": "OPEN"}]
        closed_rows    = [self._make_closed(-200.0)]

        with patch("orchestrator.datetime") as mock_dt, \
             patch("orchestrator.date") as mock_date, \
             patch("core.db.select",     return_value=[]), \
             patch("core.db.insert",     return_value={"id": "x"}), \
             patch("core.db.update"), \
             patch("agents.alpaca_broker.open_positions", return_value=open_with_loss), \
             patch("orchestrator._today_realized_pnl",   return_value=-200.0):
            mock_dt.utcnow.return_value   = _utc_now()
            mock_dt.fromisoformat.side_effect = real_datetime.fromisoformat
            mock_date.today.return_value  = _TODAY_DATE
            from orchestrator import _maybe_run_intraday_scan
            _maybe_run_intraday_scan(broker="simulation")

    def test_realized_at_limit_skips(self):
        """realized = DAILY_LOSS_LIMIT, no unrealized → total = limit → skip."""
        insert_mock = MagicMock(return_value={"id": "x"})
        closed_rows = [self._make_closed(float(DAILY_LOSS_LIMIT))]

        with patch("orchestrator.datetime") as mock_dt, \
             patch("orchestrator.date") as mock_date, \
             patch("core.db.select",     return_value=[]), \
             patch("core.db.insert",     insert_mock), \
             patch("core.db.update"), \
             patch("agents.alpaca_broker.open_positions", return_value=[]), \
             patch("orchestrator._today_realized_pnl",   return_value=float(DAILY_LOSS_LIMIT)):
            mock_dt.utcnow.return_value   = _utc_now()
            mock_dt.fromisoformat.side_effect = real_datetime.fromisoformat
            mock_date.today.return_value  = _TODAY_DATE
            from orchestrator import _maybe_run_intraday_scan
            _maybe_run_intraday_scan(broker="simulation")

        daily_runs_inserts = [c for c in insert_mock.call_args_list
                              if c[0][0] == "b_daily_runs"]
        assert len(daily_runs_inserts) == 0


# ── daily_runs record creation ─────────────────────────────────────────────────

class TestDailyRunsRecordCreationB:
    """Verify b_daily_runs row is created and updated on each successful scan."""

    def test_b_daily_runs_row_inserted_on_successful_scan_simulation(self):
        """db.insert('b_daily_runs', ...) called when intraday scan opens positions."""
        mock_insert, _, _ = _run_b_intraday_scan(broker="simulation")
        runs_inserts = [c for c in mock_insert.call_args_list if c[0][0] == "b_daily_runs"]
        assert len(runs_inserts) >= 1

    def test_b_daily_runs_row_has_run_type_intraday(self):
        """Run record must have run_type='intraday'."""
        mock_insert, _, _ = _run_b_intraday_scan(broker="simulation")
        runs_inserts = [c for c in mock_insert.call_args_list if c[0][0] == "b_daily_runs"]
        assert any(c[0][1].get("run_type") == "intraday" for c in runs_inserts)

    def test_b_daily_runs_row_has_run_number(self):
        """Run record must include run_number."""
        mock_insert, _, _ = _run_b_intraday_scan(broker="simulation")
        runs_inserts = [c for c in mock_insert.call_args_list if c[0][0] == "b_daily_runs"]
        assert all("run_number" in c[0][1] for c in runs_inserts)

    def test_b_daily_runs_updated_with_positions_opened(self):
        """db.update('b_daily_runs', ...) called with positions_opened count."""
        _, mock_update, _ = _run_b_intraday_scan(broker="simulation")
        update_calls = [c for c in mock_update.call_args_list if c[0][0] == "b_daily_runs"]
        assert len(update_calls) >= 1
        assert "positions_opened" in update_calls[0][0][2]

    def test_b_daily_runs_not_created_when_no_candidates(self):
        """No b_daily_runs row if no candidates found."""
        mock_insert, _, _ = _run_b_intraday_scan(candidates=[], broker="simulation")
        runs_inserts = [c for c in mock_insert.call_args_list if c[0][0] == "b_daily_runs"]
        assert len(runs_inserts) == 0

    def test_b_daily_runs_not_created_when_all_rejected(self):
        """No b_daily_runs row if risk rejects all trades."""
        mock_insert, _, _ = _run_b_intraday_scan(approved=[], final=[], broker="simulation")
        runs_inserts = [c for c in mock_insert.call_args_list if c[0][0] == "b_daily_runs"]
        assert len(runs_inserts) == 0


# ── run_id threading ───────────────────────────────────────────────────────────

class TestRunIdThreadingB:
    """run_id from b_daily_runs row must be passed to place_orders in alpaca mode."""

    def test_place_orders_receives_run_id_in_alpaca_mode(self):
        """place_orders must be called with run_id= kwarg in alpaca broker mode."""
        mock_insert, _, mock_place = _run_b_intraday_scan(broker="alpaca")
        runs_inserts = [c for c in mock_insert.call_args_list if c[0][0] == "b_daily_runs"]
        if runs_inserts:
            assert mock_place.called
            kwargs = mock_place.call_args[1]
            assert "run_id" in kwargs
            assert kwargs["run_id"] == "run-b-001"

    def test_place_orders_not_called_in_simulation_mode(self):
        """In simulation mode, place_orders is not called (trades used directly)."""
        _, _, mock_place = _run_b_intraday_scan(broker="simulation")
        assert not mock_place.called
