"""Tests for _get_gap_up_tickers() in orchestrator.py"""
from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import sys


def _make_mover(symbol: str, pct: float, price: float = 100.0):
    return SimpleNamespace(symbol=symbol, percent_change=pct, price=price)


def _mock_screener_context(gainers, raise_on_init=False):
    """Return a context manager stack that mocks ScreenerClient inside orchestrator."""
    mock_movers = MagicMock()
    mock_movers.gainers = gainers

    mock_client_instance = MagicMock()
    mock_client_instance.get_market_movers.return_value = mock_movers

    mock_screener_cls = MagicMock()
    if raise_on_init:
        mock_screener_cls.side_effect = Exception("network")
    else:
        mock_screener_cls.return_value = mock_client_instance

    screener_mod = MagicMock()
    screener_mod.ScreenerClient = mock_screener_cls
    return screener_mod


def _run(gainers, min_gap_pct=2.0, raise_on_init=False):
    screener_mod = _mock_screener_context(gainers, raise_on_init=raise_on_init)
    saved = sys.modules.get("alpaca.data.historical.screener")
    sys.modules["alpaca.data.historical.screener"] = screener_mod
    try:
        # reimport to pick up mock in local-import functions
        from orchestrator import _get_gap_up_tickers
        return _get_gap_up_tickers(min_gap_pct=min_gap_pct)
    finally:
        if saved is None:
            sys.modules.pop("alpaca.data.historical.screener", None)
        else:
            sys.modules["alpaca.data.historical.screener"] = saved


class TestGetGapUpTickers:

    def test_returns_list(self):
        result = _run([_make_mover("AAPL", 3.5)])
        assert isinstance(result, list)

    def test_filters_below_min_gap(self):
        gainers = [_make_mover("AAPL", 3.5), _make_mover("MSFT", 1.0)]
        result = _run(gainers, min_gap_pct=2.0)
        tickers = [r["ticker"] for r in result]
        assert "AAPL" in tickers
        assert "MSFT" not in tickers

    def test_exact_min_gap_included(self):
        result = _run([_make_mover("AAPL", 2.0)], min_gap_pct=2.0)
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"

    def test_result_fields(self):
        result = _run([_make_mover("NVDA", 5.2, price=800.0)], min_gap_pct=2.0)
        assert result[0]["ticker"] == "NVDA"
        assert result[0]["gap_pct"] == 5.2
        assert result[0]["price"] == 800.0

    def test_empty_gainers(self):
        result = _run([], min_gap_pct=2.0)
        assert result == []

    def test_none_gainers_returns_empty(self):
        screener_mod = MagicMock()
        mock_movers = MagicMock()
        mock_movers.gainers = None
        screener_mod.ScreenerClient.return_value.get_market_movers.return_value = mock_movers

        saved = sys.modules.get("alpaca.data.historical.screener")
        sys.modules["alpaca.data.historical.screener"] = screener_mod
        try:
            from orchestrator import _get_gap_up_tickers
            result = _get_gap_up_tickers()
        finally:
            if saved is None:
                sys.modules.pop("alpaca.data.historical.screener", None)
            else:
                sys.modules["alpaca.data.historical.screener"] = saved
        assert result == []

    def test_screener_exception_returns_empty(self):
        result = _run([], raise_on_init=True)
        assert result == []
