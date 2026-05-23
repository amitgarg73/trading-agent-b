"""Tests for agents/pool_scorer.py"""
import pytest
from agents.pool_scorer import _compute_daily_score, _compute_rolling_score
from config.settings import (
    SCORE_WEIGHT_WIN_LOSS, SCORE_WEIGHT_PNL, SCORE_WEIGHT_SLIPPAGE,
    SCORE_WEIGHT_SETUP,
)
from unittest.mock import patch, MagicMock


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


def test_unfilled_analysis_prints_outcome():
    """_print_unfilled_analysis should report whether target would have been hit."""
    from agents.pool_scorer import _print_unfilled_analysis
    import pandas as pd
    from datetime import date

    today = str(date.today())
    unfilled = [{
        "ticker": "AAPL", "close_reason": "UNFILLED",
        "entry_price": 180.0, "target_price": 182.0, "stop_loss": 178.8,
        "opened_at": f"{today}T10:00:00",
    }]

    fake_hist = pd.DataFrame([
        {"High": 183.0, "Low": 179.0, "Close": 182.5, "Open": 180.0, "Volume": 1_000_000}
    ])

    with patch("agents.pool_scorer.yf.Ticker") as mock_yf:
        mock_yf.return_value.history.return_value = fake_hist
        # Should not raise; target 182.0 ≤ high 183.0 → would hit
        _print_unfilled_analysis(unfilled)
        mock_yf.assert_called_once_with("AAPL")


def test_unfilled_analysis_skips_non_unfilled():
    """_print_unfilled_analysis must be a no-op when no UNFILLED positions."""
    from agents.pool_scorer import _print_unfilled_analysis

    closed = [{"ticker": "AAPL", "close_reason": "TARGET",
               "entry_price": 180.0, "target_price": 182.0}]
    with patch("agents.pool_scorer.yf.Ticker") as mock_yf:
        _print_unfilled_analysis(closed)
        mock_yf.assert_not_called()


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


# ── _alpaca_order_pnl reconciliation ────────────────────────────────────────

def _make_order(order_id, cid, filled_price, filled_qty, legs=None):
    o = MagicMock()
    o.id = order_id
    o.client_order_id = cid
    o.side = "buy"
    o.filled_avg_price = filled_price
    o.filled_qty = filled_qty
    o.legs = legs or []
    return o


def _make_leg(status, filled_price):
    leg = MagicMock()
    leg.status = status
    leg.filled_avg_price = filled_price
    return leg


@patch("agents.alpaca_broker._get")
def test_b_alpaca_order_pnl_bracket_exit(mock_get):
    """Bracket exit: P&L from entry fill + exit leg fill."""
    import pytest
    buy = _make_order("ord-b1", "stratb_AAPL_20260523120000", 180.0, 15,
                      legs=[_make_leg("filled", 183.6)])
    mock_get.return_value.get_orders.return_value = [buy]

    from agents.pool_scorer import _alpaca_order_pnl
    pnl, note = _alpaca_order_pnl("stratb_", [])

    assert pnl == pytest.approx((183.6 - 180.0) * 15, abs=0.01)
    assert "1b" in note


@patch("agents.alpaca_broker._get")
def test_b_alpaca_order_pnl_manual_fallback(mock_get):
    """Manual close with no exit leg: falls back to DB realized_pnl."""
    import pytest
    buy = _make_order("ord-b2", "stratb_MSFT_20260523130000", 400.0, 8, legs=[])
    mock_get.return_value.get_orders.return_value = [buy]

    positions = [{"alpaca_order_id": "ord-b2", "realized_pnl": 120.0}]
    from agents.pool_scorer import _alpaca_order_pnl
    pnl, note = _alpaca_order_pnl("stratb_", positions)

    assert pnl == pytest.approx(120.0, abs=0.01)
    assert "1m" in note


@patch("agents.alpaca_broker._get")
def test_b_alpaca_order_pnl_wrong_tag_returns_none(mock_get):
    """Orders tagged for strategy A must not match strategy B prefix."""
    buy = _make_order("ord-b3", "strata_NVDA_ts", 500.0, 5)
    mock_get.return_value.get_orders.return_value = [buy]

    from agents.pool_scorer import _alpaca_order_pnl
    pnl, note = _alpaca_order_pnl("stratb_", [])

    assert pnl is None
    assert "no tagged" in note
