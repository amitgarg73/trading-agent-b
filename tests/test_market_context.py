"""
Tests for agents/market_context.py (Strategy B)
Covers: sector rotation returned in get(), sorted order, graceful error handling.
All yfinance calls are mocked — no network access.
"""
from __future__ import annotations
import pandas as pd
import pytest
from unittest.mock import patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_sector_df(etfs: list[str], chg: float = 0.01) -> pd.DataFrame:
    """Build a fake yf.download multi-ticker DataFrame (2 rows, all ETFs same chg)."""
    cols = pd.MultiIndex.from_tuples(
        [(etf, col) for etf in etfs for col in ["Close", "Volume"]],
    )
    data = {}
    for etf in etfs:
        data[(etf, "Close")]  = [100.0, 100.0 * (1 + chg)]
        data[(etf, "Volume")] = [1_000_000, 1_000_000]
    return pd.DataFrame(data, index=pd.date_range("2026-01-01", periods=2))


def _make_vix_df(level: float = 18.0) -> pd.DataFrame:
    return pd.DataFrame({"Close": [level, level]},
                        index=pd.date_range("2026-01-01", periods=2))


def _make_spy_df(chg: float = 0.003) -> pd.DataFrame:
    spy_close = [500.0, 500.0 * (1 + chg)]
    return pd.DataFrame({"Close": spy_close},
                        index=pd.date_range("2026-01-01", periods=2))


# ── sector_rotation key in get() ──────────────────────────────────────────────

class TestSectorRotationInGet:

    def _call_get(self, sector_df=None, vix_df=None, spy_df=None):
        from agents.market_context import _SECTOR_ETFS
        etfs = _SECTOR_ETFS

        if sector_df is None:
            sector_df = _make_sector_df(etfs, chg=0.01)
        if vix_df is None:
            vix_df = _make_vix_df()
        if spy_df is None:
            spy_df = _make_spy_df()

        def _fake_download(symbols, *args, **kwargs):
            # sector rotation call passes a list; VIX/SPY passes a string
            if isinstance(symbols, list):
                return sector_df
            return pd.DataFrame()

        def _fake_ticker(sym):
            t = type("T", (), {})()
            if sym == "^VIX":
                t.history = lambda **kw: vix_df
            elif sym == "SPY":
                t.history = lambda **kw: spy_df
            else:
                t.history = lambda **kw: pd.DataFrame()
            return t

        with patch("agents.market_context.yf.Ticker", side_effect=_fake_ticker), \
             patch("agents.market_context.yf.download", side_effect=_fake_download), \
             patch("agents.market_context.requests.get") as mock_fg:
            mock_fg.return_value.status_code = 200
            mock_fg.return_value.json.return_value = {
                "fear_and_greed": {"score": 55, "rating": "Neutral"}
            }
            from agents.market_context import get
            return get()

    def test_sector_rotation_key_present(self):
        result = self._call_get()
        assert "sector_rotation" in result

    def test_sector_rotation_is_dict(self):
        result = self._call_get()
        assert isinstance(result["sector_rotation"], dict)

    def test_sector_rotation_sorted_best_to_worst(self):
        from agents.market_context import _SECTOR_ETFS
        # Give different chg values so sort order is meaningful
        etfs = _SECTOR_ETFS
        data = {}
        for i, etf in enumerate(etfs):
            chg = (len(etfs) - i) * 0.001  # descending: first ETF has highest chg
            data[(etf, "Close")]  = [100.0, 100.0 * (1 + chg)]
            data[(etf, "Volume")] = [1_000_000, 1_000_000]
        df = pd.DataFrame(data, index=pd.date_range("2026-01-01", periods=2))
        result = self._call_get(sector_df=df)
        values = list(result["sector_rotation"].values())
        assert values == sorted(values, reverse=True), "Sector rotation must be sorted best→worst"

    def test_sector_rotation_empty_on_download_error(self):
        """If yf.download raises, sector_rotation must be {} not an exception."""
        def _fake_ticker(sym):
            t = type("T", (), {})()
            if sym == "^VIX":
                t.history = lambda **kw: _make_vix_df()
            elif sym == "SPY":
                t.history = lambda **kw: _make_spy_df()
            else:
                t.history = lambda **kw: pd.DataFrame()
            return t

        with patch("agents.market_context.yf.Ticker", side_effect=_fake_ticker), \
             patch("agents.market_context.yf.download", side_effect=Exception("network error")), \
             patch("agents.market_context.requests.get") as mock_fg:
            mock_fg.return_value.status_code = 200
            mock_fg.return_value.json.return_value = {
                "fear_and_greed": {"score": 55, "rating": "Neutral"}
            }
            from agents.market_context import get
            result = get()
        assert result["sector_rotation"] == {}
