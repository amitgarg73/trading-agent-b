"""
Tests for Gap 7: eval_b.py metrics computation and gate check.

_compute_metrics() and _gate_check() are tested directly without DB calls;
run_eval() is tested by mocking db.select.
"""
import pytest
from unittest.mock import patch
from datetime import date, timedelta

TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


def _perf_row(dt=TODAY, pnl=600.0, win_rate=0.80, pool=None):
    return {"date": dt, "gross_pnl": pnl, "win_rate": win_rate, "pool": pool}


def _pos(ticker="AAPL", pnl=100.0, status="CLOSED", reason="TARGET",
         entry=150.0, target=153.0, stop=148.5, conf="HIGH", dt=TODAY):
    return {
        "ticker": ticker, "status": status, "realized_pnl": pnl,
        "close_reason": reason, "date": dt,
        "entry_price": entry, "target_price": target, "stop_loss": stop,
        "confidence": conf,
    }


class TestComputeMetrics:

    def _run(self, perf_rows, positions):
        from eval_b import _compute_metrics
        return _compute_metrics(perf_rows, positions)

    def test_returns_empty_for_no_data(self):
        assert self._run([], []) == {}

    def test_returns_empty_when_only_pool_rows(self):
        """Pool rows (pool != None) don't count as total rows."""
        assert self._run([_perf_row(pool=1)], []) == {}

    def test_basic_pnl_fields(self):
        ev = self._run([_perf_row(pnl=600.0, win_rate=0.75)],
                       [_pos(pnl=200.0), _pos("MSFT", pnl=-50.0)])
        assert ev["days"] == 1
        assert ev["total_pnl"] == pytest.approx(600.0)
        assert ev["avg_daily_pnl"] == pytest.approx(600.0)
        assert ev["win_days"] == 1

    def test_win_day_rate(self):
        rows = [_perf_row(dt=TODAY, pnl=500.0), _perf_row(dt=YESTERDAY, pnl=-100.0)]
        ev = self._run(rows, [])
        assert ev["win_days"] == 1
        assert ev["win_day_rate"] == pytest.approx(0.5)

    def test_grade_a_when_score_above_80(self):
        """Full marks: avg_pnl = target, 100% win days, 100% win rate → grade A."""
        from config.settings import DAILY_PROFIT_TARGET
        rows = [_perf_row(pnl=float(DAILY_PROFIT_TARGET), win_rate=1.0)]
        ev = self._run(rows, [_pos(pnl=300.0)])
        assert ev["grade"] == "A"
        assert ev["score"] >= 80

    def test_grade_d_when_all_losing(self):
        rows = [_perf_row(pnl=-200.0, win_rate=0.0)]
        ev = self._run(rows, [_pos(pnl=-100.0)])
        assert ev["grade"] in ("C", "D")

    def test_orphaned_positions_detected(self):
        """OPEN positions from a prior date are flagged as orphaned."""
        old_open = {"ticker": "XYZ", "status": "OPEN", "date": YESTERDAY}
        ev = self._run([_perf_row()], [old_open])
        assert len(ev["orphaned"]) == 1
        assert ev["orphaned"][0]["ticker"] == "XYZ"

    def test_no_orphan_for_todays_open(self):
        today_open = {"ticker": "XYZ", "status": "OPEN", "date": TODAY}
        ev = self._run([_perf_row()], [today_open])
        assert ev["orphaned"] == []

    def test_duplicate_ticker_same_day_detected(self):
        pos1 = _pos("AAPL", dt=TODAY)
        pos2 = _pos("AAPL", dt=TODAY)
        ev = self._run([_perf_row()], [pos1, pos2])
        assert ev["duplicate_count"] >= 1

    def test_rr_violation_detected(self):
        """Trade with entry=150, target=151 (0.67%), stop=148.5 → R:R < MIN_REWARD_RISK."""
        low_rr_pos = _pos(entry=150.0, target=151.0, stop=148.5)
        ev = self._run([_perf_row()], [low_rr_pos])
        assert len(ev["rr_violations"]) >= 1

    def test_confidence_cohort_computed(self):
        high_win  = _pos("AAPL", pnl=200.0, conf="HIGH")
        high_loss = _pos("MSFT", pnl=-50.0, conf="HIGH")
        med_win   = _pos("GOOG", pnl=100.0, conf="MEDIUM")
        ev = self._run([_perf_row()], [high_win, high_loss, med_win])
        assert "HIGH" in ev["confidence_stats"]
        assert ev["confidence_stats"]["HIGH"]["count"] == 2
        assert "MEDIUM" in ev["confidence_stats"]

    def test_unfilled_excluded_from_win_rate_calc(self):
        closed = _pos("AAPL", pnl=100.0)
        unfilled = {**_pos("MSFT"), "close_reason": "UNFILLED", "status": "CLOSED"}
        ev = self._run([_perf_row()], [closed, unfilled])
        # UNFILLED should count in total_attempted but not in closed P&L
        assert ev["unfilled_count"] == 1
        assert ev["total_attempted"] == 2


class TestGateCheck:

    def _gate(self, **overrides):
        from eval_b import _gate_check
        from config.settings import DAILY_PROFIT_TARGET
        base = {
            "avg_daily_pnl": float(DAILY_PROFIT_TARGET) + 100,
            "win_day_rate": 0.85,
            "avg_win_rate": 70.0,
            "grade": "A",
            "score": 85.0,
            "orphaned": [],
            "duplicate_count": 0,
            "rr_violations": [],
            "unfill_pct": 5.0,
            "actual_rr": 2.5,
            "close_reasons": {"TARGET": 10},
        }
        return _gate_check({**base, **overrides})

    def test_all_criteria_pass(self):
        passed, failures, warnings = self._gate()
        assert passed
        assert failures == []

    def test_low_avg_pnl_fails(self):
        passed, failures, _ = self._gate(avg_daily_pnl=100.0)
        assert not passed
        assert any("avg daily" in f.lower() or "target" in f.lower() for f in failures)

    def test_low_win_day_rate_fails(self):
        passed, failures, _ = self._gate(win_day_rate=0.50)
        assert not passed
        assert any("win day" in f.lower() for f in failures)

    def test_low_trade_win_rate_fails(self):
        passed, failures, _ = self._gate(avg_win_rate=40.0)
        assert not passed
        assert any("win rate" in f.lower() for f in failures)

    def test_bad_grade_fails(self):
        passed, failures, _ = self._gate(grade="B", score=65.0)
        assert not passed
        assert any("grade" in f.lower() or "score" in f.lower() for f in failures)

    def test_orphaned_positions_fail(self):
        passed, failures, _ = self._gate(orphaned=[{"ticker": "XYZ"}])
        assert not passed
        assert any("orphan" in f.lower() for f in failures)

    def test_duplicates_fail(self):
        passed, failures, _ = self._gate(duplicate_count=2)
        assert not passed
        assert any("duplicate" in f.lower() for f in failures)

    def test_rr_violations_fail(self):
        passed, failures, _ = self._gate(rr_violations=[{"ticker": "ABC", "rr": 0.5}])
        assert not passed
        assert any("r:r" in f.lower() or "reward" in f.lower() for f in failures)

    def test_high_unfill_warns_not_fails(self):
        passed, failures, warnings = self._gate(unfill_pct=20.0)
        assert passed          # high unfill is a WARNING, not a hard failure
        assert any("unfill" in w.lower() for w in warnings)


class TestRunEval:

    def test_run_eval_returns_empty_on_no_data(self):
        with patch("eval_b.db") as mock_db:
            mock_db.select.return_value = []
            from eval_b import run_eval
            result = run_eval(days=14)
            assert result == {}

    def test_run_eval_passes_good_data(self):
        from config.settings import DAILY_PROFIT_TARGET
        perf_rows = [_perf_row(pnl=float(DAILY_PROFIT_TARGET) + 200, win_rate=0.9)]
        positions = [_pos(pnl=300.0)]
        with patch("eval_b.db") as mock_db:
            def _sel(table, **kw):
                if "performance" in table:
                    return perf_rows
                return positions
            mock_db.select.side_effect = _sel
            from eval_b import run_eval
            result = run_eval(days=None)
            assert "passed" in result

    def test_run_eval_gate_fails_low_pnl(self):
        perf_rows = [_perf_row(pnl=-100.0, win_rate=0.3)]
        positions = [_pos(pnl=-50.0)]
        with patch("eval_b.db") as mock_db:
            def _sel(table, **kw):
                if "performance" in table:
                    return perf_rows
                return positions
            mock_db.select.side_effect = _sel
            from eval_b import run_eval
            result = run_eval(days=None)
            assert not result.get("passed")
            assert len(result.get("failures", [])) > 0
