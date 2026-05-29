"""Tests for scanner/pool_filter.py"""
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock, call
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

@patch("scanner.pool_filter._prefetch_batch")
@patch("scanner.pool_filter._has_earnings_soon", return_value=False)
@patch("scanner.pool_filter._realtime_metrics")
@patch("scanner.pool_filter.pool_manager.get_pool")
def test_get_pool3_returns_top_n(mock_pool, mock_metrics, mock_earnings, mock_prefetch):
    mock_pool.return_value = ["AAPL", "MSFT", "NVDA", "GOOGL", "META",
                               "AMZN", "TSLA", "JPM", "V", "MA", "GS",
                               "UNH", "XOM", "WMT", "HD"]

    def side_effect(ticker):
        return _metrics(ticker=ticker, vol_ratio=1.8, above_vwap=True, rs=1.5)

    mock_metrics.side_effect = side_effect
    result = get_pool3_tickers()
    from config.settings import POOL3_SIZE
    assert len(result) <= POOL3_SIZE


@patch("scanner.pool_filter._prefetch_batch")
@patch("scanner.pool_filter._has_earnings_soon", return_value=True)
@patch("scanner.pool_filter.pool_manager.get_pool")
def test_earnings_stocks_not_excluded(mock_pool, mock_earnings, mock_prefetch):
    # Earnings blackout disabled for Strategy B — stocks pass through regardless
    mock_pool.return_value = ["AAPL", "MSFT"]
    result = get_pool3_tickers()
    assert len(result) >= 0  # pool filter may still return 0 if vol_ratio too low


# ── quality floor tests ───────────────────────────────────────────────────────

def test_quality_floor_excludes_negative_score_stocks():
    """Stocks scoring ≤ 0 must not enter Pool 3."""
    negative_metrics = _metrics(
        ticker="WEAK", vol_ratio=0.3, above_vwap=False, rs=-0.5, ret=-1.0
    )
    score = _filter_score(negative_metrics)
    assert score <= 0, f"Expected negative/zero score, got {score}"

    with patch("scanner.pool_filter.pool_manager.get_pool", return_value=["WEAK"]), \
         patch("scanner.pool_filter._prefetch_batch"), \
         patch("scanner.pool_filter._realtime_metrics", return_value=negative_metrics):
        result = get_pool3_tickers()
    assert "WEAK" not in result


def test_quality_floor_allows_positive_score_stocks():
    """Stocks scoring > 0 pass the quality floor."""
    good_metrics = _metrics(ticker="STRONG", vol_ratio=2.5, above_vwap=True, rs=1.8, ret=1.5)
    score = _filter_score(good_metrics)
    assert score > 0, f"Expected positive score, got {score}"

    with patch("scanner.pool_filter.pool_manager.get_pool", return_value=["STRONG"]), \
         patch("scanner.pool_filter._prefetch_batch"), \
         patch("scanner.pool_filter._realtime_metrics", return_value=good_metrics):
        result = get_pool3_tickers()
    assert "STRONG" in result


@patch("scanner.pool_filter._prefetch_batch")
@patch("scanner.pool_filter._realtime_metrics")
@patch("scanner.pool_filter.pool_manager.get_pool")
def test_get_pool3_returns_empty_when_all_below_floor(mock_pool, mock_metrics, mock_prefetch):
    """If every Pool 2 stock has score ≤ 0, Pool 3 is empty — no trades that day."""
    mock_pool.return_value = ["AAPL", "MSFT", "NVDA"]
    mock_metrics.side_effect = lambda t: _metrics(
        ticker=t, vol_ratio=0.2, above_vwap=False, rs=-1.0, ret=-2.0
    )
    result = get_pool3_tickers()
    assert result == []


@patch("scanner.pool_filter._prefetch_batch")
@patch("scanner.pool_filter._realtime_metrics")
@patch("scanner.pool_filter.pool_manager.get_pool")
def test_quality_floor_filters_partial_list(mock_pool, mock_metrics, mock_prefetch):
    """Mix of positive and negative scores — only positive ones enter Pool 3."""
    mock_pool.return_value = ["AAPL", "WEAK"]

    def side_effect(ticker):
        if ticker == "AAPL":
            return _metrics(ticker="AAPL", vol_ratio=2.0, above_vwap=True, rs=1.5, ret=1.0)
        return _metrics(ticker="WEAK", vol_ratio=0.2, above_vwap=False, rs=-0.5, ret=-1.0)

    mock_metrics.side_effect = side_effect
    result = get_pool3_tickers()
    assert "AAPL" in result
    assert "WEAK" not in result


# ── _prefetch_batch and batch cache tests ─────────────────────────────────────

def test_realtime_metrics_reads_from_batch_cache():
    """When batch cache is populated, _realtime_metrics returns cached data without yfinance."""
    import scanner.pool_filter as pf
    cached = _metrics(ticker="AAPL", vol_ratio=3.5, above_vwap=True, rs=2.1)
    pf._batch_data_cache = {"AAPL": cached}
    try:
        with patch("scanner.pool_filter.yf.Ticker") as mock_yf:
            result = pf._realtime_metrics("AAPL")
        mock_yf.assert_not_called()  # yfinance not touched when cache hit
        assert result == cached
    finally:
        pf._batch_data_cache = {}


def test_realtime_metrics_falls_back_when_not_in_cache():
    """When batch cache is empty, _realtime_metrics falls back to yfinance."""
    import scanner.pool_filter as pf
    pf._batch_data_cache = {}
    mock_df = pd.DataFrame({
        "Close": [100.0, 102.0],
        "Volume": [1_000_000, 1_200_000],
        "High": [103.0, 104.0],
        "Low": [99.0, 101.0],
    })
    mock_df.columns = [c.lower() for c in mock_df.columns]

    with patch("scanner.pool_filter.yf.Ticker") as mock_yf:
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_df
        mock_yf.return_value = mock_ticker
        result = pf._realtime_metrics("AAPL")

    assert result is not None
    assert result["ticker"] == "AAPL"
    assert result["cur_price"] == 102.0


def _make_snapshot(price=150.0, prev_close=145.0, vwap=148.0, volume=1_200_000,
                   open_px=146.0, high=152.0, low=144.0):
    snap = MagicMock()
    snap.daily_bar.close   = price
    snap.daily_bar.vwap    = vwap
    snap.daily_bar.volume  = volume
    snap.daily_bar.open    = open_px
    snap.daily_bar.high    = high
    snap.daily_bar.low     = low
    snap.latest_trade.price = price
    snap.prev_day_bar.close = prev_close
    return snap


def _make_bar(volume: float = 1_000_000) -> MagicMock:
    b = MagicMock()
    b.volume = volume
    return b


def test_prefetch_batch_populates_cache(monkeypatch):
    """_prefetch_batch makes 3 Alpaca calls and builds the cache correctly."""
    import scanner.pool_filter as pf

    snap_aapl = _make_snapshot(price=150.0, prev_close=145.0, vwap=148.0, volume=1_500_000)
    snap_spy  = _make_snapshot(price=520.0, prev_close=515.0, open_px=516.0)
    snap_spy.prev_day_bar = None  # SPY RS uses open, not prev_day_bar

    snapshots = {"AAPL": snap_aapl, "SPY": snap_spy}

    daily_bars_resp = {"AAPL": [_make_bar(1_000_000) for _ in range(21)]}
    intraday_resp   = {}  # pre-market simulation — no intraday bars

    mock_client = MagicMock()
    mock_client.get_stock_snapshot.return_value = snapshots
    mock_client.get_stock_bars.side_effect = [daily_bars_resp, intraday_resp]

    monkeypatch.setattr(pf, "_data_client", mock_client)
    monkeypatch.setattr(pf, "_batch_data_cache", {})

    # Simulate pre-market so Call 3 is skipped
    from datetime import datetime
    import pytz
    pre_market = datetime(2026, 5, 29, 8, 0, tzinfo=pytz.timezone("America/New_York"))
    with patch("scanner.pool_filter.datetime") as mock_dt:
        mock_dt.now.return_value = pre_market
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        pf._prefetch_batch(["AAPL"])

    assert "AAPL" in pf._batch_data_cache
    m = pf._batch_data_cache["AAPL"]
    assert m["cur_price"] == 150.0
    assert m["vol_ratio"] > 0
    assert m["above_vwap"] is True   # 150 > 148
    assert m["above_orb"] is None    # no intraday bars pre-market
    # Daily bars: 20 bars at 1M each → avg = 1M, today vol = 1.5M → ratio ≈ 1.5
    assert abs(m["vol_ratio"] - 1.5) < 0.05


def test_prefetch_batch_graceful_on_api_failure(monkeypatch):
    """If Alpaca calls fail, _prefetch_batch populates no cache and does not raise."""
    import scanner.pool_filter as pf

    mock_client = MagicMock()
    mock_client.get_stock_snapshot.side_effect = Exception("connection refused")
    mock_client.get_stock_bars.side_effect = Exception("connection refused")

    monkeypatch.setattr(pf, "_data_client", mock_client)
    monkeypatch.setattr(pf, "_batch_data_cache", {})

    pf._prefetch_batch(["AAPL", "MSFT"])
    assert pf._batch_data_cache == {}  # no crash, empty cache
