"""Tests for agents/pool_scorer.py"""
import pytest
from agents.pool_scorer import _compute_daily_score, _compute_rolling_score
from config.settings import (
    SCORE_WEIGHT_WIN_LOSS, SCORE_WEIGHT_PNL, SCORE_WEIGHT_SLIPPAGE,
    SCORE_WEIGHT_SETUP,
)
from unittest.mock import patch


def test_win_scores_higher_than_loss():
    win_score  = _compute_daily_score(win=True,  pnl=150.0, slippage_bps=2.0,  setup_score=7.0)
    loss_score = _compute_daily_score(win=False, pnl=-80.0, slippage_bps=10.0, setup_score=4.0)
    assert win_score > loss_score


def test_score_bounded_0_to_10():
    best  = _compute_daily_score(win=True,  pnl=500.0,  slippage_bps=0.0, setup_score=10.0)
    worst = _compute_daily_score(win=False, pnl=-500.0, slippage_bps=25.0, setup_score=0.0)
    assert 0 <= worst <= 10
    assert 0 <= best  <= 10


def test_no_trade_score_is_neutral():
    score = _compute_daily_score(win=None, pnl=None, slippage_bps=None, setup_score=5.0)
    assert 4.0 <= score <= 6.0  # should be around middle


def test_high_slippage_lowers_score():
    low_slip  = _compute_daily_score(win=True, pnl=100.0, slippage_bps=0.0,  setup_score=7.0)
    high_slip = _compute_daily_score(win=True, pnl=100.0, slippage_bps=20.0, setup_score=7.0)
    assert low_slip > high_slip


def test_slippage_computed_from_fill_price():
    """score_today() should use fill_price vs entry_price, not hardcode 0."""
    from unittest.mock import patch, MagicMock
    from agents.pool_scorer import score_today
    from datetime import date

    today = str(date.today())
    fake_position = {
        "ticker":       "AAPL",
        "pool":         3,
        "entry_price":  180.00,   # planned
        "fill_price":   180.36,   # actual fill — 20 bps slip
        "close_price":  183.00,
        "realized_pnl": 300.0,
        "closed_at":    f"{today}T15:30:00",
        "status":       "CLOSED",
    }

    with patch("agents.pool_scorer.db.select") as mock_select, \
         patch("agents.pool_scorer.db.upsert") as mock_upsert, \
         patch("agents.pool_scorer.pool_manager.update_trade_stats"), \
         patch("agents.pool_scorer.pool_manager.apply_promotions_demotions", return_value={"promoted": [], "demoted": []}), \
         patch("agents.pool_scorer.pool_manager.get_pool", return_value=[]):

        def select_side(table, **kwargs):
            if table == "b_positions":
                return [fake_position]
            return []

        mock_select.side_effect = select_side

        score_today()

        # Find the upsert call for AAPL and check slippage_bps is non-zero
        calls = mock_upsert.call_args_list
        aapl_call = next((c for c in calls if c.args[1].get("ticker") == "AAPL"), None)
        assert aapl_call is not None
        slip = aapl_call.args[1].get("slippage_bps", 0)
        assert slip > 0, f"Expected non-zero slippage, got {slip}"


@patch("agents.pool_scorer.db.select")
def test_rolling_score_empty_history_returns_today(mock_select):
    mock_select.return_value = []
    rolling = _compute_rolling_score("AAPL", today_score=7.5)
    assert rolling == 7.5


@patch("agents.pool_scorer.db.select")
def test_rolling_score_averages_history(mock_select):
    from datetime import date, timedelta
    yesterday = str(date.today() - timedelta(days=1))
    mock_select.return_value = [{"date": yesterday, "daily_score": 5.0, "ticker": "AAPL"}]
    rolling = _compute_rolling_score("AAPL", today_score=9.0)
    # Recent day (yesterday) gets 2x weight, today gets 2x weight
    # weighted avg of [9.0*2, 5.0*2] / (2+2) = (18+10)/4 = 7.0
    assert 6.0 <= rolling <= 9.0  # between the two values
