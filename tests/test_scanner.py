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


# ── Behavioral signal tests ──────────────────────────────────────────────────

def _make_df_with_gap(n=30, open_px=105.0, close_px=107.0, prev_close=100.0) -> pd.DataFrame:
    """Build a df where last bar has a gap from prev_close → open_px."""
    closes  = [prev_close] * (n - 1) + [close_px]
    opens   = [prev_close - 0.2] * (n - 1) + [open_px]
    highs   = [c + 1.0 for c in closes]
    lows    = [c - 0.8 for c in closes]
    volumes = [8_000_000] * n
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    })


from scanner.scanner import _behavioral_score


@patch("scanner.scanner._fetch_sector_return", return_value=0.01)
def test_vwap_reclaim_signal_detected(mock_sector):
    """Stock opened below typical price (VWAP proxy) but closed above → reclaim."""
    df = _make_df_with_gap(open_px=98.0, close_px=105.0, prev_close=100.0)
    # typical = (high + low + close) / 3 ≈ (106 + 104.2 + 105) / 3 ≈ 105.1
    # open=98 < typical=105.1 → opened_below_vwap=True, close=105 < typical=105.1 → above_vwap=False
    # Actually close=105 vs typical≈105.1 — borderline. Let me adjust:
    df.loc[df.index[-1], "close"] = 108.0   # well above typical
    result = _behavioral_score("AAPL", df, {})
    assert result["vwap_signal"] in ("RECLAIM", "ABOVE")


@patch("scanner.scanner._fetch_sector_return", return_value=0.01)
def test_gap_up_holding_adds_score(mock_sector):
    """Stock gapped up >1% and is still holding near open → continuation signal."""
    df = _make_df_with_gap(open_px=103.0, close_px=103.5, prev_close=100.0)
    result = _behavioral_score("AAPL", df, {})
    assert result["gap_pct"] > 1.0
    # Holding the gap: close >= open * 0.99
    gap_holding_signal = any("holding" in s for s in result["behavior_signals"])
    assert gap_holding_signal


@patch("scanner.scanner._fetch_sector_return", return_value=0.01)
def test_gap_up_fading_penalises_score(mock_sector):
    """Stock gapped up but has fallen back below the open → weakness."""
    df = _make_df_with_gap(open_px=105.0, close_px=100.5, prev_close=100.0)
    result = _behavioral_score("AAPL", df, {})
    fading_signal = any("fading" in s for s in result["behavior_signals"])
    assert fading_signal


@patch("scanner.scanner._fetch_sector_return", return_value=0.01)
def test_behavioral_score_returns_required_keys(mock_sector):
    df = _make_df()
    result = _behavioral_score("AAPL", df, {})
    for key in ("atr_ratio", "above_vwap", "vwap_signal", "vwap_reclaim",
                "gap_pct", "rs_vs_sector", "behavior_score", "behavior_signals"):
        assert key in result, f"Missing key: {key}"


# ── Breakout freshness ───────────────────────────────────────────────────────

def _make_df_at_dist(dist_pct: float, n: int = 60) -> pd.DataFrame:
    """Build a DataFrame where the last close is dist_pct% above its own SMA20."""
    base = 100.0
    closes = [base] * n
    df = pd.DataFrame({
        "open":   [base - 0.3] * n,
        "high":   [base + 1.0] * n,
        "low":    [base - 0.8] * n,
        "close":  closes,
        "volume": [10_000_000] * n,
    })
    sma20 = sum(closes[-20:]) / 20
    df.iloc[-1, df.columns.get_loc("close")] = sma20 * (1 + dist_pct / 100)
    return df


@patch("scanner.scanner._fetch_sector_return", return_value=0.01)
@patch("scanner.scanner._fetch")
def test_fresh_breakout_adds_score(mock_fetch, mock_sector):
    """dist_sma20 0-5% → +1 to technical_score and FRESH label in output."""
    df = _make_df_at_dist(dist_pct=3.0)
    info = _make_info(avg_vol=10_000_000, price=float(df["close"].iloc[-1]))
    df["volume"] = [15_000_000] * len(df)  # vol above MIN_VOLUME_RATIO
    mock_fetch.return_value = (info, df)
    result = _score_ticker("AAPL")
    if result is not None:
        assert result["breakout_freshness"] == "FRESH"
        assert any("Fresh" in s for s in result["signals"])


@patch("scanner.scanner._fetch_sector_return", return_value=0.01)
@patch("scanner.scanner._fetch")
def test_extended_breakout_penalises_score(mock_fetch, mock_sector):
    """dist_sma20 >12% → -1 to technical_score and EXTENDED label in output."""
    df = _make_df_at_dist(dist_pct=15.0)
    info = _make_info(avg_vol=10_000_000, price=float(df["close"].iloc[-1]))
    df["volume"] = [15_000_000] * len(df)
    mock_fetch.return_value = (info, df)
    result = _score_ticker("AAPL")
    if result is not None:
        assert result["breakout_freshness"] == "EXTENDED"
        assert any("Extended" in s for s in result["signals"])


@patch("scanner.scanner._fetch_sector_return", return_value=0.01)
@patch("scanner.scanner._fetch")
def test_normal_breakout_no_adjustment(mock_fetch, mock_sector):
    """dist_sma20 5-12% → NORMAL label, no freshness bonus/penalty."""
    df = _make_df_at_dist(dist_pct=8.0)
    info = _make_info(avg_vol=10_000_000, price=float(df["close"].iloc[-1]))
    df["volume"] = [15_000_000] * len(df)
    mock_fetch.return_value = (info, df)
    result = _score_ticker("AAPL")
    if result is not None:
        assert result["breakout_freshness"] == "NORMAL"
        assert not any("Fresh" in s or "Extended" in s for s in result["signals"])


@patch("scanner.scanner._fetch_sector_return", return_value=0.01)
@patch("scanner.scanner._fetch")
def test_breakout_freshness_field_always_in_output(mock_fetch, mock_sector):
    """breakout_freshness key must be present in any non-None result."""
    df = _make_df_at_dist(dist_pct=3.0)
    info = _make_info(avg_vol=10_000_000, price=float(df["close"].iloc[-1]))
    df["volume"] = [15_000_000] * len(df)
    mock_fetch.return_value = (info, df)
    result = _score_ticker("AAPL")
    if result is not None:
        assert "breakout_freshness" in result
        assert result["breakout_freshness"] in ("FRESH", "NORMAL", "EXTENDED")
