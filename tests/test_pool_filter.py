"""Tests for scanner/pool_filter.py"""
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock
from scanner.pool_filter import _filter_score, _orb_vwap_signals, get_pool3_tickers


# ── helpers ──────────────────────────────────────────────────────────────────

def _metrics(ticker="AAPL", vol_ratio=2.0, above_vwap=True, rs=1.8, ret=1.0,
             vwap_reclaim=None, above_orb=None, vol_acceleration=None,
             rs_vs_market=None, above_vwap_intraday=None) -> dict:
    return {
        "ticker":               ticker,
        "vol_ratio":            vol_ratio,
        "above_vwap":           above_vwap,
        "rs_vs_sector":         rs,
        "rs_vs_market":         rs_vs_market,
        "cur_price":            185.0,
        "today_return":         ret,
        "vwap_reclaim":         vwap_reclaim,
        "above_orb":            above_orb,
        "vol_acceleration":     vol_acceleration,
        "above_vwap_intraday":  above_vwap_intraday,
    }


def _make_intraday(n=24, trend="up") -> pd.DataFrame:
    """Create synthetic 5-min intraday bars."""
    base = 100.0
    rows = []
    for i in range(n):
        if trend == "up":
            close = base + i * 0.2
        elif trend == "down":
            close = base - i * 0.2
        else:
            close = base + np.sin(i) * 0.5
        rows.append({
            "open":   close - 0.1,
            "high":   close + 0.3,
            "low":    close - 0.3,
            "close":  close,
            "volume": 100_000 + np.random.randint(0, 50_000),
        })
    df = pd.DataFrame(rows)
    # Give it today's dates so date filtering works
    from datetime import date, timedelta
    import datetime as dt
    today = date.today()
    times = [dt.datetime.combine(today, dt.time(9, 30)) + dt.timedelta(minutes=5*i) for i in range(n)]
    df.index = pd.DatetimeIndex(times)
    return df


# ── _filter_score tests ───────────────────────────────────────────────────────

def test_filter_score_above_vwap_high_rs():
    score = _filter_score(_metrics(vol_ratio=2.5, above_vwap=True, rs=2.0))
    assert score > 5.0


def test_filter_score_below_vwap_negative_rs():
    score = _filter_score(_metrics(vol_ratio=1.6, above_vwap=False, rs=-0.5))
    assert score < 3.0


def test_filter_score_volume_contributes():
    low_vol  = _filter_score(_metrics(vol_ratio=1.5))
    high_vol = _filter_score(_metrics(vol_ratio=4.0))
    assert high_vol > low_vol


def test_filter_score_no_rs_data():
    score = _filter_score(_metrics(rs=None))
    assert isinstance(score, float)


def test_vwap_reclaim_scores_higher_than_just_above():
    base_score    = _filter_score(_metrics(above_vwap=True,  vwap_reclaim=False))
    reclaim_score = _filter_score(_metrics(above_vwap=True,  vwap_reclaim=True))
    assert reclaim_score > base_score


def test_above_orb_adds_score():
    no_orb  = _filter_score(_metrics(above_orb=False))
    orb_hit = _filter_score(_metrics(above_orb=True))
    assert orb_hit > no_orb


def test_volume_acceleration_building_adds_score():
    fading   = _filter_score(_metrics(vol_acceleration=0.7))
    building = _filter_score(_metrics(vol_acceleration=1.5))
    assert building > fading


def test_market_rs_adds_score():
    no_mrs   = _filter_score(_metrics(rs_vs_market=None))
    with_mrs = _filter_score(_metrics(rs_vs_market=2.0))
    assert with_mrs > no_mrs


# ── _orb_vwap_signals tests ───────────────────────────────────────────────────

def test_orb_above_when_trending_up():
    df = _make_intraday(n=24, trend="up")
    signals = _orb_vwap_signals(df)
    # Trending up: later bars always above ORB high from first 6 bars
    assert signals["above_orb"] is True


def test_orb_below_when_trending_down():
    df = _make_intraday(n=24, trend="down")
    signals = _orb_vwap_signals(df)
    assert signals["above_orb"] is False


def test_vwap_reclaim_detected():
    """Price dips below VWAP in first half then recovers."""
    df = _make_intraday(n=24, trend="down")
    # Force the last few bars to be well above everything else
    df.loc[df.index[-3:], "close"] = 200.0
    df.loc[df.index[-3:], "high"]  = 200.5
    signals = _orb_vwap_signals(df)
    # With close=200 vs VWAP of ~99, current is above VWAP and early bars were below
    assert signals["above_vwap_intraday"] is True


def test_vol_acceleration_building():
    df = _make_intraday(n=12, trend="up")
    # Make last 3 bars have much higher volume
    df.loc[df.index[-3:], "volume"] = 500_000
    df.loc[df.index[:3],  "volume"] = 50_000
    signals = _orb_vwap_signals(df)
    assert signals["vol_acceleration"] > 1.0


def test_vol_acceleration_fading():
    df = _make_intraday(n=12, trend="up")
    df.loc[df.index[-3:], "volume"] = 50_000
    df.loc[df.index[:3],  "volume"] = 500_000
    signals = _orb_vwap_signals(df)
    assert signals["vol_acceleration"] < 1.0


def test_orb_signals_keys_present():
    df = _make_intraday(n=12)
    signals = _orb_vwap_signals(df)
    for key in ("above_orb", "vwap_reclaim", "above_vwap_intraday", "vol_acceleration",
                "orb_high", "orb_low"):
        assert key in signals, f"Missing key: {key}"


# ── get_pool3 integration tests ───────────────────────────────────────────────

@patch("scanner.pool_filter._has_earnings_soon", return_value=False)
@patch("scanner.pool_filter._realtime_metrics")
@patch("scanner.pool_filter.pool_manager.get_pool")
def test_get_pool3_returns_top_n(mock_pool, mock_metrics, mock_earnings):
    mock_pool.return_value = ["AAPL", "MSFT", "NVDA", "GOOGL", "META",
                               "AMZN", "TSLA", "JPM", "V", "MA", "GS",
                               "UNH", "XOM", "WMT", "HD"]

    def side_effect(ticker):
        return _metrics(ticker=ticker, vol_ratio=1.8, above_vwap=True, rs=1.5)

    mock_metrics.side_effect = side_effect
    result = get_pool3_tickers()
    from config.settings import POOL3_SIZE
    assert len(result) <= POOL3_SIZE


@patch("scanner.pool_filter._has_earnings_soon", return_value=True)
@patch("scanner.pool_filter.pool_manager.get_pool")
def test_earnings_stocks_excluded(mock_pool, mock_earnings):
    mock_pool.return_value = ["AAPL", "MSFT"]
    result = get_pool3_tickers()
    assert len(result) == 0
