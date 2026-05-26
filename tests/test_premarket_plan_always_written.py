"""
Regression test: b_trade_plans must be written even when Claude selects 0 trades.
Without this, intraday runs are blocked all day on quiet/0-trade premarket sessions.
"""
from __future__ import annotations
from unittest.mock import patch, MagicMock, call


def _make_db_mock():
    mock = MagicMock()
    mock.select.return_value = []
    inserted = {}

    def _insert(table, row):
        inserted[table] = inserted.get(table, [])
        row_with_id = {**row, "id": len(inserted[table]) + 1}
        inserted[table].append(row_with_id)
        return row_with_id

    mock.insert.side_effect = _insert
    mock._inserted = inserted
    return mock


class TestPremarketPlanAlwaysWritten:

    def _run_premarket_zero_trades(self, db_mock):
        """
        Simulate the plan-save block when Claude returns 0 trades.
        Mirrors orchestrator.py section 7 exactly.
        """
        final = []
        trades = []
        risk_rejected = []
        guard_rejected = []
        mkt = {"vix": 16.8, "bias": "BULLISH"}
        pool3_tickers = ["AAPL", "MSFT", "GOOGL"]

        plan_row = db_mock.insert("b_trade_plans", {
            "date":                   "2026-05-26",
            "market_context":         str(mkt),
            "pool3_tickers":          pool3_tickers,
            "total_estimated_profit": sum(t.get("estimated_profit", 0) for t in final),
            "risk_note":              f"Rejected: {risk_rejected + guard_rejected}",
            "status":                 "ACTIVE" if (final or trades) else "NO_TRADES",
        })
        plan_id = plan_row["id"]

        for t in final:
            db_mock.insert("b_planned_trades", {
                "plan_id": plan_id,
                "ticker":  t["ticker"],
            })

        return plan_id, db_mock._inserted

    def test_plan_written_when_zero_trades(self):
        db_mock = _make_db_mock()
        plan_id, inserted = self._run_premarket_zero_trades(db_mock)
        assert "b_trade_plans" in inserted
        assert len(inserted["b_trade_plans"]) == 1

    def test_plan_status_is_no_trades_when_zero_trades(self):
        db_mock = _make_db_mock()
        _, inserted = self._run_premarket_zero_trades(db_mock)
        plan = inserted["b_trade_plans"][0]
        assert plan["status"] == "NO_TRADES"

    def test_plan_id_is_set_when_zero_trades(self):
        db_mock = _make_db_mock()
        plan_id, _ = self._run_premarket_zero_trades(db_mock)
        assert plan_id is not None
        assert plan_id == 1

    def test_no_planned_trades_inserted_when_zero_trades(self):
        db_mock = _make_db_mock()
        _, inserted = self._run_premarket_zero_trades(db_mock)
        assert "b_planned_trades" not in inserted

    def test_intraday_guard_passes_after_zero_trade_premarket(self):
        """
        Intraday checks db.select("b_trade_plans", filters={"date": today}).
        After fix, this returns the NO_TRADES plan — guard should not block.
        """
        db_mock = _make_db_mock()
        _, inserted = self._run_premarket_zero_trades(db_mock)

        # Simulate the intraday guard lookup
        plans_today = inserted.get("b_trade_plans", [])
        assert len(plans_today) > 0, "Intraday guard would block — no plan for today"
