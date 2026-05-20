"""Tests for scanner/pool_filter.py"""
import pytest
from unittest.mock import patch, MagicMock
from scanner.pool_filter import _filter_score, get_pool3_tickers


def _metrics(ticker="AAPL", vol_ratio=2.0, above_vwap=True, rs=1.8, ret=1.0) -> dict:
    return {
        "ticker":       ticker,
        "vol_ratio":    vol_ratio,
        "above_vwap":   above_vwap,
        "rs_vs_sector": rs,
        "cur_price":    185.0,
        "today_return": ret,
    }


def test_filter_score_above_vwap_high_rs():
    score = _filter_score(_metrics(vol_ratio=2.5, above_vwap=True, rs=2.0))
    assert score > 5.0  # should score well


def test_filter_score_below_vwap_negative_rs():
    score = _filter_score(_metrics(vol_ratio=1.6, above_vwap=False, rs=-0.5))
    assert score < 3.0  # should score poorly


def test_filter_score_volume_contributes():
    low_vol  = _filter_score(_metrics(vol_ratio=1.5))
    high_vol = _filter_score(_metrics(vol_ratio=4.0))
    assert high_vol > low_vol


def test_filter_score_no_rs_data():
    score = _filter_score(_metrics(rs=None))
    # Should not crash and should still produce a score
    assert isinstance(score, float)


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
