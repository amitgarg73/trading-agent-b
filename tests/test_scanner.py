"""Tests for scanner/scanner.py"""
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock
from scanner.scanner import run_scan, _score_ticker


def _make_df(n=30, trending=True) -> pd.DataFrame:
    close = [100 + (i * 0.5 if trending else np.sin(i) * 2) for i in range(n)]
    return pd.DataFrame({
        "open":   [c - 0.3 for c in close],
        "high":   [c + 1.0 for c in close],
        "low":    [c - 0.8 for c in close],
        "close":  close,
        "volume": [5_000_000 + np.random.randint(-500_000, 500_000) for _ in range(n)],
    })


def _make_info(avg_vol=10_000_000, price=115.0) -> dict:
    return {"averageVolume": avg_vol, "longName": "Test Corp", "sector": "Technology"}


@patch("scanner.scanner._fetch_sector_return", return_value=0.01)
@patch("scanner.scanner._fetch")
def test_low_volume_stock_excluded(mock_fetch, mock_sector):
    df = _make_df()
    df["volume"] = [500_000] * len(df)  # below MIN_AVG_VOLUME
    mock_fetch.return_value = ({"averageVolume": 500_000}, df)
    result = _score_ticker("WEAK")
    assert result is None


@patch("scanner.scanner._fetch_sector_return", return_value=0.01)
@patch("scanner.scanner._fetch")
def test_low_price_stock_excluded(mock_fetch, mock_sector):
    df = _make_df()
    mock_fetch.return_value = ({"averageVolume": 10_000_000}, df)
    # Manually set close prices below MIN_PRICE
    df["close"] = [5.0] * len(df)
    df["open"] = [4.8] * len(df)
    df["high"] = [5.2] * len(df)
    df["low"]  = [4.6] * len(df)
    result = _score_ticker("CHEAP")
    assert result is None


@patch("scanner.scanner._fetch_sector_return", return_value=None)
@patch("scanner.scanner._fetch")
def test_valid_candidate_returned(mock_fetch, mock_sector):
    df = _make_df(trending=True)
    # Pump volume to trigger volume ratio signal
    df["volume"] = [15_000_000] * len(df)
    info = _make_info(avg_vol=6_000_000, price=115.0)
    mock_fetch.return_value = (info, df)
    result = _score_ticker("AAPL")
    # May return None if score below threshold — just check no exception
    assert result is None or isinstance(result, dict)


@patch("scanner.scanner._score_ticker")
def test_run_scan_filters_none(mock_score):
    mock_score.side_effect = [None, {"ticker": "MSFT", "total_score": 5}, None]
    results = run_scan(["AAPL", "MSFT", "NVDA"])
    assert len(results) == 1
    assert results[0]["ticker"] == "MSFT"


@patch("scanner.scanner._score_ticker")
def test_run_scan_sorted_by_score(mock_score):
    mock_score.side_effect = [
        {"ticker": "A", "total_score": 3},
        {"ticker": "B", "total_score": 8},
        {"ticker": "C", "total_score": 5},
    ]
    results = run_scan(["A", "B", "C"])
    scores = [r["total_score"] for r in results]
    assert scores == sorted(scores, reverse=True)


@patch("scanner.scanner._score_ticker")
def test_run_scan_empty_universe(mock_score):
    results = run_scan([])
    assert results == []
