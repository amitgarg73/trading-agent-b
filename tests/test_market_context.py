"""
Tests for agents/market_context.py (Strategy B)
SPY and sector rotation now use Alpaca daily bars; VIX still uses yfinance.
"""
from __future__ import annotations
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock


def _make_vix_df(level: float = 18.0) -> pd.DataFrame:
    return pd.DataFrame({"Close": [level, level]},
                        index=pd.date_range("2026-01-01", periods=2))


def _make_fake_bars(tickers, prices_by_ticker: dict[str, list[float]]):
    """Build a fake get_stock_bars() return value."""
    from types import SimpleNamespace
    data = {}
    for ticker, prices in prices_by_ticker.items():
        bars = []
        for p in prices:
            b = SimpleNamespace(close=p, high=p * 1.01, low=p * 0.99)
            bars.append(b)
        data[ticker] = bars
    result = MagicMock()
    result.data = data
    return result


class TestFetchSpyChange:

    def _call(self, bars_result):
        mock_client = MagicMock()
        mock_client.get_stock_bars.return_value = bars_result
        with patch("agents.alpaca_broker._dclient", return_value=mock_client):
            from agents.market_context import _fetch_spy_change
            return _fetch_spy_change()

    def test_bullish_when_spy_up_more_than_0_2_pct(self):
        bars = _make_fake_bars(["SPY"], {"SPY": [500.0, 501.5]})  # +0.3%
        bias, chg = self._call(bars)
        assert bias == "BULLISH"
        assert chg is not None and chg > 0

    def test_bearish_when_spy_down_more_than_0_2_pct(self):
        bars = _make_fake_bars(["SPY"], {"SPY": [500.0, 498.0]})  # -0.4%
        bias, chg = self._call(bars)
        assert bias == "BEARISH"
        assert chg is not None and chg < 0

    def test_neutral_when_spy_flat(self):
        bars = _make_fake_bars(["SPY"], {"SPY": [500.0, 500.5]})  # +0.1%
        bias, chg = self._call(bars)
        assert bias == "NEUTRAL"

    def test_neutral_on_alpaca_error(self):
        mock_client = MagicMock()
        mock_client.get_stock_bars.side_effect = Exception("network")
        with patch("agents.alpaca_broker._dclient", return_value=mock_client):
            from agents.market_context import _fetch_spy_change
            bias, chg = _fetch_spy_change()
        assert bias == "NEUTRAL"
        assert chg is None

    def test_neutral_when_fewer_than_2_bars(self):
        bars = _make_fake_bars(["SPY"], {"SPY": [500.0]})
        bias, chg = self._call(bars)
        assert bias == "NEUTRAL"
        assert chg is None


class TestFetchSectorRotation:

    def _call(self, bars_result):
        mock_client = MagicMock()
        mock_client.get_stock_bars.return_value = bars_result
        with patch("agents.alpaca_broker._dclient", return_value=mock_client):
            from agents.market_context import _fetch_sector_rotation
            return _fetch_sector_rotation()

    def test_returns_dict(self):
        from agents.market_context import _SECTOR_ETFS
        prices = {etf: [100.0, 101.0] for etf in _SECTOR_ETFS}
        bars = _make_fake_bars(_SECTOR_ETFS, prices)
        result = self._call(bars)
        assert isinstance(result, dict)

    def test_sorted_best_to_worst(self):
        from agents.market_context import _SECTOR_ETFS
        prices = {etf: [100.0, 100.0 + (i + 1) * 0.5] for i, etf in enumerate(_SECTOR_ETFS)}
        bars = _make_fake_bars(_SECTOR_ETFS, prices)
        result = self._call(bars)
        values = list(result.values())
        assert values == sorted(values, reverse=True)

    def test_empty_on_alpaca_error(self):
        mock_client = MagicMock()
        mock_client.get_stock_bars.side_effect = Exception("network")
        with patch("agents.alpaca_broker._dclient", return_value=mock_client):
            from agents.market_context import _fetch_sector_rotation
            result = _fetch_sector_rotation()
        assert result == {}

    def test_skips_etf_with_fewer_than_2_bars(self):
        from agents.market_context import _SECTOR_ETFS
        prices = {etf: [100.0] for etf in _SECTOR_ETFS}  # only 1 bar each
        bars = _make_fake_bars(_SECTOR_ETFS, prices)
        result = self._call(bars)
        assert result == {}


class TestGet:

    def _call_get(self, vix_level=18.0, spy_bias="BULLISH", spy_chg=0.3,
                  sector_rotation=None, fg_score=55):
        if sector_rotation is None:
            sector_rotation = {"XLK": 1.0, "XLF": -0.5}

        def _fake_ticker(sym):
            t = type("T", (), {})()
            if sym == "^VIX":
                t.history = lambda **kw: _make_vix_df(vix_level)
            else:
                t.history = lambda **kw: pd.DataFrame()
            return t

        with patch("agents.market_context.yf.Ticker", side_effect=_fake_ticker), \
             patch("agents.market_context._fetch_spy_change", return_value=(spy_bias, spy_chg)), \
             patch("agents.market_context._fetch_sector_rotation", return_value=sector_rotation), \
             patch("agents.market_context.requests.get") as mock_fg:
            mock_fg.return_value.status_code = 200
            mock_fg.return_value.json.return_value = {
                "fear_and_greed": {"score": fg_score, "rating": "Neutral"}
            }
            from agents.market_context import get
            return get()

    def test_vix_returned(self):
        result = self._call_get(vix_level=20.5)
        assert result["vix_level"] == 20.5

    def test_futures_bias_returned(self):
        result = self._call_get(spy_bias="BEARISH", spy_chg=-0.5)
        assert result["futures_bias"] == "BEARISH"
        assert result["spy_change_pct"] == -0.5

    def test_fear_greed_returned(self):
        result = self._call_get(fg_score=30)
        assert result["fear_greed"] == 30

    def test_sector_rotation_present(self):
        result = self._call_get(sector_rotation={"XLK": 1.5, "XLF": -0.3})
        assert result["sector_rotation"] == {"XLK": 1.5, "XLF": -0.3}

    def test_vix_none_on_error(self):
        with patch("agents.market_context.yf.Ticker", side_effect=Exception("fail")), \
             patch("agents.market_context._fetch_spy_change", return_value=("NEUTRAL", None)), \
             patch("agents.market_context._fetch_sector_rotation", return_value={}), \
             patch("agents.market_context.requests.get") as mock_fg:
            mock_fg.return_value.status_code = 200
            mock_fg.return_value.json.return_value = {
                "fear_and_greed": {"score": 50, "rating": "Neutral"}
            }
            from agents.market_context import get
            result = get()
        assert result.get("vix_level") is None
