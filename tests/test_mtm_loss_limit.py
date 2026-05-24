"""
Tests for Gap 4: mark-to-market loss limit in agents/risk.py

Pre-fix: validate() only checked realized P&L — open positions bleeding
         unrealized losses were invisible to the loss limit.
Post-fix: _today_net_pnl() = realized + unrealized; loss limit fires on MTM.
"""
import pytest
from unittest.mock import patch
from datetime import date

TODAY = date.today().isoformat()

def _make_closed(pnl: float, ticker: str = "AAPL") -> dict:
    return {"ticker": ticker, "status": "CLOSED", "realized_pnl": pnl,
            "closed_at": f"{TODAY}T15:00:00"}

def _make_open(ticker: str, unrealized: float) -> dict:
    return {"ticker": ticker, "status": "OPEN", "unrealized_pnl": unrealized,
            "date": TODAY}

def _run_validate(closed_pos, open_pos, trades=None):
    trades = trades or [{"ticker": "JNJ", "action": "BUY", "entry_price": 155.0,
                          "target_price": 157.3, "stop_loss": 153.9,
                          "confidence": "MEDIUM", "position_size": 3000,
                          "reward_risk": 1.5}]
    all_pos = closed_pos + open_pos
    with patch("agents.risk.db") as mock_db:
        mock_db.select.side_effect = lambda table, **kw: (
            [p for p in open_pos]   if kw.get("filters", {}).get("status") == "OPEN"
            else [p for p in closed_pos]
        )
        from agents.risk import validate
        return validate(trades)


class TestMTMLossLimit:

    def test_realized_only_passes_without_unrealized(self):
        """If realized P&L is fine and no open positions, trades are approved."""
        approved, rejected = _run_validate(
            closed_pos=[_make_closed(100.0)],
            open_pos=[],
        )
        assert len(approved) > 0
        assert not any("loss limit" in r for r in rejected)

    def test_realized_loss_alone_triggers_limit(self):
        """Realized loss exceeding limit blocks new trades (baseline behavior preserved)."""
        approved, rejected = _run_validate(
            closed_pos=[_make_closed(-600.0)],
            open_pos=[],
        )
        assert approved == []
        assert any("loss limit" in r.lower() or "Daily loss" in r for r in rejected)

    def test_unrealized_loss_triggers_limit(self):
        """Open positions with large unrealized loss should trigger the limit
        even when realized P&L is $0."""
        approved, rejected = _run_validate(
            closed_pos=[],
            open_pos=[_make_open("AAPL", -400.0), _make_open("MSFT", -200.0)],
        )
        # Total unrealized = -$600, which is below DAILY_LOSS_LIMIT (-$500)
        assert approved == []
        assert any("loss limit" in r.lower() or "Daily loss" in r for r in rejected)

    def test_combined_realized_and_unrealized_triggers_limit(self):
        """Realized -$300 + unrealized -$300 = -$600 MTM should block trades."""
        approved, rejected = _run_validate(
            closed_pos=[_make_closed(-300.0)],
            open_pos=[_make_open("AAPL", -300.0)],
        )
        assert approved == []
        assert any("loss limit" in r.lower() or "Daily loss" in r for r in rejected)

    def test_unrealized_gain_offsets_realized_loss(self):
        """Realized -$400 but unrealized +$200 = -$200 MTM — should NOT block."""
        approved, rejected = _run_validate(
            closed_pos=[_make_closed(-400.0)],
            open_pos=[_make_open("AAPL", 200.0)],
        )
        # MTM = -$200, above DAILY_LOSS_LIMIT (-$500)
        assert len(approved) > 0

    def test_today_net_pnl_function_directly(self):
        """_today_net_pnl returns realized + unrealized sum."""
        open_pos = [_make_open("AAPL", -200.0), _make_open("MSFT", -100.0)]
        with patch("agents.risk.db") as mock_db:
            mock_db.select.return_value = [_make_closed(-150.0)]
            from agents.risk import _today_net_pnl
            result = _today_net_pnl(open_pos)
        assert result == pytest.approx(-150.0 + -200.0 + -100.0)

    def test_mtm_label_in_rejection_reason(self):
        """Rejection reason should mention MTM to distinguish from old realized-only check."""
        approved, rejected = _run_validate(
            closed_pos=[_make_closed(-600.0)],
            open_pos=[],
        )
        assert any("MTM" in r for r in rejected)
