"""Tests for scanner/intraday_momentum.py"""
from __future__ import annotations
from unittest.mock import patch, MagicMock
import pytest
from scanner import intraday_momentum
from config.settings import (
    SCORE_THRESHOLD, MIN_INTRADAY_MOVE_PCT, MIN_SPY_MOVE_PCT, STRONG_SECTOR_THRESHOLD,
    STALE_MOVE_THRESHOLD_PCT, FRESH_MOMENTUM_MIN_PCT,
)

SPY_UP   = {"today_pct_change": 0.5,  "above_vwap": True,  "rs_vs_spy": None, "vwap": 550.0, "change_pct_15m": 0.1}
SPY_DOWN = {"today_pct_change": -0.5, "above_vwap": False, "rs_vs_spy": None, "vwap": 550.0, "change_pct_15m": -0.1}
XLK_HOT  = {"today_pct_change": STRONG_SECTOR_THRESHOLD + 1.0, "above_vwap": True, "rs_vs_spy": None, "vwap": 200.0, "change_pct_15m": 0.5}
XLK_FLAT = {"today_pct_change": 0.1,  "above_vwap": True,  "rs_vs_spy": None, "vwap": 200.0, "change_pct_15m": 0.0}


# ---------------------------------------------------------------------------
# _momentum_score
# ---------------------------------------------------------------------------

def test_momentum_score_minimum_is_score_threshold():
    score = intraday_momentum._momentum_score(0.1, None)
    assert score >= SCORE_THRESHOLD


def test_momentum_score_increases_with_pct():
    s1 = intraday_momentum._momentum_score(2.0,  None)
    s2 = intraday_momentum._momentum_score(6.0,  None)
    s3 = intraday_momentum._momentum_score(10.0, None)
    assert s1 < s2 < s3


def test_momentum_score_rs_bonus():
    base = intraday_momentum._momentum_score(4.0, None)
    with_rs = intraday_momentum._momentum_score(4.0, 2.0)
    assert with_rs == base + 1


def test_momentum_score_rs_below_threshold_no_bonus():
    base = intraday_momentum._momentum_score(4.0, None)
    no_bonus = intraday_momentum._momentum_score(4.0, 1.5)
    assert no_bonus == base


def test_momentum_score_capped_at_10():
    score = intraday_momentum._momentum_score(100.0, 5.0)
    assert score == 10


# ---------------------------------------------------------------------------
# scan_alpaca
# ---------------------------------------------------------------------------

def test_scan_alpaca_returns_candidates_above_threshold():
    signals = {
        "SPY":  SPY_UP,
        "AAPL": {"today_pct_change": 3.0, "above_vwap": True, "rs_vs_spy": 1.5, "vwap": 180.0, "change_pct_15m": 0.5},
    }
    live_prices = {"AAPL": 182.0}
    with patch("agents.alpaca_broker.get_intraday_signals", return_value=signals), \
         patch("agents.alpaca_broker.get_live_prices", return_value=live_prices):
        result = intraday_momentum.scan_alpaca(["AAPL"])
    assert len(result) == 1
    assert result[0]["ticker"] == "AAPL"
    assert result[0]["signal_type"] == "INTRADAY_MOMENTUM"


def test_scan_alpaca_spy_gate_blocks_when_market_and_sectors_down():
    # SPY negative AND all sector ETFs flat — both gates fail
    signals = {
        "SPY":  SPY_DOWN,
        "AAPL": {"today_pct_change": 1.0, "above_vwap": True, "rs_vs_spy": 1.0, "vwap": 180.0, "change_pct_15m": 0.4},
    }
    with patch("agents.alpaca_broker.get_intraday_signals", return_value=signals), \
         patch("agents.alpaca_broker.get_live_prices", return_value={}):
        result = intraday_momentum.scan_alpaca(["AAPL"])
    assert result == []


def test_scan_alpaca_sector_override_passes_when_spy_negative():
    # SPY negative but XLK strongly up — sector gate overrides, AAPL candidate passes
    signals = {
        "SPY":  SPY_DOWN,
        "XLK":  XLK_HOT,
        "AAPL": {"today_pct_change": 2.0, "above_vwap": True, "rs_vs_spy": 1.5, "vwap": 180.0, "change_pct_15m": 0.5},
    }
    with patch("agents.alpaca_broker.get_intraday_signals", return_value=signals), \
         patch("agents.alpaca_broker.get_live_prices", return_value={"AAPL": 182.0}):
        result = intraday_momentum.scan_alpaca(["AAPL"])
    assert len(result) == 1
    assert result[0]["ticker"] == "AAPL"


def test_scan_alpaca_sector_below_threshold_does_not_override():
    # SPY negative AND XLK only barely up — sector gate not met
    signals = {
        "SPY":  SPY_DOWN,
        "XLK":  XLK_FLAT,
        "AAPL": {"today_pct_change": 2.0, "above_vwap": True, "rs_vs_spy": 1.5, "vwap": 180.0, "change_pct_15m": 0.5},
    }
    with patch("agents.alpaca_broker.get_intraday_signals", return_value=signals), \
         patch("agents.alpaca_broker.get_live_prices", return_value={"AAPL": 182.0}):
        result = intraday_momentum.scan_alpaca(["AAPL"])
    assert result == []


def test_scan_alpaca_filters_below_min_move():
    signals = {
        "SPY":  SPY_UP,
        "AAPL": {"today_pct_change": 0.3, "above_vwap": True, "rs_vs_spy": 1.0, "vwap": 180.0, "change_pct_15m": 0.1},
    }
    with patch("agents.alpaca_broker.get_intraday_signals", return_value=signals), \
         patch("agents.alpaca_broker.get_live_prices", return_value={}):
        result = intraday_momentum.scan_alpaca(["AAPL"])
    assert result == []


def test_min_move_for_regime_mapping():
    assert intraday_momentum.min_move_for_regime("CHOPPY") == 0.3
    assert intraday_momentum.min_move_for_regime("TREND") == 0.5
    assert intraday_momentum.min_move_for_regime("FEAR") == 0.75
    assert intraday_momentum.min_move_for_regime(None) == MIN_INTRADAY_MOVE_PCT
    assert intraday_momentum.min_move_for_regime("UNKNOWN") == MIN_INTRADAY_MOVE_PCT


def test_scan_alpaca_choppy_regime_admits_smaller_move():
    # A 0.3% mover is filtered by the default 0.5% bar but admitted on a CHOPPY day (0.3% bar).
    signals = {
        "SPY":  SPY_UP,
        "AAPL": {"today_pct_change": 0.3, "above_vwap": True, "rs_vs_spy": 1.0, "vwap": 180.0, "change_pct_15m": 0.1},
    }
    with patch("agents.alpaca_broker.get_intraday_signals", return_value=signals), \
         patch("agents.alpaca_broker.get_live_prices", return_value={"AAPL": 181.0}):
        assert len(intraday_momentum.scan_alpaca(["AAPL"], regime="CHOPPY")) == 1
        assert intraday_momentum.scan_alpaca(["AAPL"], regime="TREND") == []


def test_scan_alpaca_filters_not_above_vwap():
    signals = {
        "SPY":  SPY_UP,
        "AAPL": {"today_pct_change": 5.0, "above_vwap": False, "rs_vs_spy": 2.0, "vwap": 180.0, "change_pct_15m": 0.5},
    }
    with patch("agents.alpaca_broker.get_intraday_signals", return_value=signals), \
         patch("agents.alpaca_broker.get_live_prices", return_value={}):
        result = intraday_momentum.scan_alpaca(["AAPL"])
    assert result == []


def test_scan_alpaca_filters_extended_move():
    signals = {
        "SPY":  SPY_UP,
        "AAPL": {"today_pct_change": 35.0, "above_vwap": True, "rs_vs_spy": 5.0, "vwap": 180.0, "change_pct_15m": 1.0},
    }
    with patch("agents.alpaca_broker.get_intraday_signals", return_value=signals), \
         patch("agents.alpaca_broker.get_live_prices", return_value={}):
        result = intraday_momentum.scan_alpaca(["AAPL"])
    assert result == []


def test_scan_alpaca_sorted_by_rs_then_pct():
    signals = {
        "SPY":  SPY_UP,
        "AAPL": {"today_pct_change": 3.0, "above_vwap": True, "rs_vs_spy": 1.0, "vwap": 180.0, "change_pct_15m": 0.5},
        "MSFT": {"today_pct_change": 2.5, "above_vwap": True, "rs_vs_spy": 3.0, "vwap": 300.0, "change_pct_15m": 0.5},
    }
    with patch("agents.alpaca_broker.get_intraday_signals", return_value=signals), \
         patch("agents.alpaca_broker.get_live_prices", return_value={}):
        result = intraday_momentum.scan_alpaca(["AAPL", "MSFT"])
    assert result[0]["ticker"] == "MSFT"


def test_scan_alpaca_uses_live_price_over_vwap():
    signals = {
        "SPY":  SPY_UP,
        "AAPL": {"today_pct_change": 3.0, "above_vwap": True, "rs_vs_spy": 1.0, "vwap": 180.0, "change_pct_15m": 0.5},
    }
    with patch("agents.alpaca_broker.get_live_prices", return_value={"AAPL": 185.0}), \
         patch("agents.alpaca_broker.get_intraday_signals", return_value=signals):
        result = intraday_momentum.scan_alpaca(["AAPL"])
    assert result[0]["entry_price"] == 185.0


# ---------------------------------------------------------------------------
# scan_simulation
# ---------------------------------------------------------------------------

def _mock_yf_df(open_px: float, close_px: float):
    import pandas as pd
    import numpy as np
    times = pd.date_range("2026-05-20 09:30", periods=10, freq="5min")
    closes = [open_px] * 5 + [close_px] * 5
    df = pd.DataFrame({
        "Open":  [open_px] * 10,
        "Close": closes,
        "High":  closes,
        "Low":   closes,
        "Volume": [1_000_000] * 10,
    }, index=times)
    return df


def test_scan_simulation_passes_mover():
    df = _mock_yf_df(100.0, 103.0)
    with patch("yfinance.download", return_value=df):
        result = intraday_momentum.scan_simulation(["AAPL"])
    assert len(result) == 1
    assert result[0]["ticker"] == "AAPL"


def test_scan_simulation_filters_below_threshold():
    df = _mock_yf_df(100.0, 100.3)  # 0.3% — below MIN_INTRADAY_MOVE_PCT (0.5%)
    with patch("yfinance.download", return_value=df):
        result = intraday_momentum.scan_simulation(["AAPL"])
    assert result == []


def test_scan_simulation_filters_below_vwap():
    df = _mock_yf_df(100.0, 96.0)  # down 4%, below vwap
    with patch("yfinance.download", return_value=df):
        result = intraday_momentum.scan_simulation(["AAPL"])
    assert result == []


def test_scan_simulation_handles_exception_gracefully():
    with patch("yfinance.download", side_effect=Exception("network")):
        result = intraday_momentum.scan_simulation(["AAPL"])
    assert result == []


# ---------------------------------------------------------------------------
# scan() entry point
# ---------------------------------------------------------------------------

def test_scan_routes_to_alpaca():
    with patch("scanner.intraday_momentum.scan_alpaca", return_value=[]) as mock_alpaca:
        intraday_momentum.scan(["AAPL"], broker="alpaca")
    mock_alpaca.assert_called_once_with(["AAPL"], regime=None)


def test_scan_routes_to_simulation():
    with patch("scanner.intraday_momentum.scan_simulation", return_value=[]) as mock_sim:
        intraday_momentum.scan(["AAPL"], broker="simulation")
    mock_sim.assert_called_once_with(["AAPL"], regime=None)


def test_scan_returns_empty_on_exception():
    with patch("scanner.intraday_momentum.scan_alpaca", side_effect=Exception("fail")):
        result = intraday_momentum.scan(["AAPL"], broker="alpaca")
    assert result == []


# ---------------------------------------------------------------------------
# pool field (strategy time-of-day fix)
# ---------------------------------------------------------------------------

def test_scan_alpaca_candidates_include_pool_2():
    """scan_alpaca must include pool: 2 so Claude applies Pool 2 time-of-day rules."""
    signals = {
        "SPY":  SPY_UP,
        "AAPL": {"today_pct_change": 4.0, "above_vwap": True, "rs_vs_spy": 2.0, "vwap": 180.0, "change_pct_15m": 0.5},
    }
    with patch("agents.alpaca_broker.get_intraday_signals", return_value=signals), \
         patch("agents.alpaca_broker.get_live_prices", return_value={"AAPL": 185.0}):
        result = intraday_momentum.scan_alpaca(["AAPL"])
    assert len(result) == 1
    assert result[0]["pool"] == 2, "momentum candidates must have pool=2 for strategy time-of-day routing"


def test_scan_simulation_candidates_include_pool_2():
    """scan_simulation must include pool: 2 on every candidate."""
    df = _mock_yf_df(100.0, 104.0)
    with patch("yfinance.download", return_value=df):
        result = intraday_momentum.scan_simulation(["AAPL"])
    assert len(result) == 1
    assert result[0]["pool"] == 2, "simulation candidates must have pool=2 for strategy time-of-day routing"


def test_strategy_system_prompt_includes_intraday_momentum_exception():
    """SYSTEM prompt must mention INTRADAY_MOMENTUM exception so Claude doesn't skip afternoon candidates."""
    from agents.strategy import SYSTEM
    assert "INTRADAY_MOMENTUM" in SYSTEM, (
        "strategy SYSTEM prompt must include INTRADAY_MOMENTUM exception in time-of-day rules"
    )


# ---------------------------------------------------------------------------
# Staleness / momentum freshness filter
# ---------------------------------------------------------------------------

def test_stale_move_filtered_when_15m_flat():
    """Stock up >STALE threshold but flat last 15m — should be dropped."""
    signals = {
        "SPY":  SPY_UP,
        "LLY":  {
            "today_pct_change": STALE_MOVE_THRESHOLD_PCT + 1.0,
            "above_vwap": True, "rs_vs_spy": 2.0, "vwap": 1100.0,
            "change_pct_15m": FRESH_MOMENTUM_MIN_PCT - 0.1,  # below fresh threshold
        },
    }
    with patch("agents.alpaca_broker.get_intraday_signals", return_value=signals), \
         patch("agents.alpaca_broker.get_live_prices", return_value={}):
        result = intraday_momentum.scan_alpaca(["LLY"])
    assert result == []


def test_stale_move_passes_when_15m_fresh():
    """Stock up >STALE threshold AND still moving last 15m — should pass."""
    signals = {
        "SPY":  SPY_UP,
        "LLY":  {
            "today_pct_change": STALE_MOVE_THRESHOLD_PCT + 1.0,
            "above_vwap": True, "rs_vs_spy": 2.0, "vwap": 1100.0,
            "change_pct_15m": FRESH_MOMENTUM_MIN_PCT + 0.1,  # above fresh threshold
        },
    }
    with patch("agents.alpaca_broker.get_intraday_signals", return_value=signals), \
         patch("agents.alpaca_broker.get_live_prices", return_value={"LLY": 1150.0}):
        result = intraday_momentum.scan_alpaca(["LLY"])
    assert len(result) == 1
    assert result[0]["ticker"] == "LLY"


def test_small_total_move_not_subject_to_freshness_filter():
    """Stock below STALE threshold passes even with flat 15m — small moves don't need freshness."""
    signals = {
        "SPY":  SPY_UP,
        "AAPL": {
            "today_pct_change": STALE_MOVE_THRESHOLD_PCT - 0.1,  # below stale threshold
            "above_vwap": True, "rs_vs_spy": 1.5, "vwap": 180.0,
            "change_pct_15m": 0.0,  # completely flat last 15m
        },
    }
    with patch("agents.alpaca_broker.get_intraday_signals", return_value=signals), \
         patch("agents.alpaca_broker.get_live_prices", return_value={"AAPL": 182.0}):
        result = intraday_momentum.scan_alpaca(["AAPL"])
    assert len(result) == 1


def test_candidate_includes_change_pct_15m_field():
    """Passing candidates must carry change_pct_15m for downstream reasoning."""
    signals = {
        "SPY":  SPY_UP,
        "AAPL": {
            "today_pct_change": 3.0, "above_vwap": True, "rs_vs_spy": 2.0,
            "vwap": 180.0, "change_pct_15m": 0.6,
        },
    }
    with patch("agents.alpaca_broker.get_intraday_signals", return_value=signals), \
         patch("agents.alpaca_broker.get_live_prices", return_value={"AAPL": 182.0}):
        result = intraday_momentum.scan_alpaca(["AAPL"])
    assert result[0]["change_pct_15m"] == 0.6


def test_missing_change_pct_15m_defaults_to_zero():
    """Signal without change_pct_15m key still filters correctly (defaults to 0.0)."""
    signals = {
        "SPY":  SPY_UP,
        # no change_pct_15m key — simulates broker returning partial data
        "LLY":  {"today_pct_change": 2.6, "above_vwap": True, "rs_vs_spy": 5.0, "vwap": 1100.0},
    }
    with patch("agents.alpaca_broker.get_intraday_signals", return_value=signals), \
         patch("agents.alpaca_broker.get_live_prices", return_value={}):
        result = intraday_momentum.scan_alpaca(["LLY"])
    # 2.6% >= STALE_MOVE_THRESHOLD_PCT and 0.0 < FRESH_MOMENTUM_MIN_PCT → filtered
    assert result == []
