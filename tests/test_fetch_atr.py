"""Tests for _fetch_atr_for_tickers in orchestrator.py (Alpaca-based ATR calculation)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch
import pytest


def _make_bar(high: float, low: float, close: float) -> MagicMock:
    b = MagicMock()
    b.high  = high
    b.low   = low
    b.close = close
    return b


def _bars_for_price(price: float = 100.0, n: int = 20, atr_abs: float = 1.0) -> list:
    """Build n daily bars with constant ATR = atr_abs dollars."""
    bars = []
    for i in range(n):
        bars.append(_make_bar(high=price + atr_abs, low=price - atr_abs, close=price))
    return bars


def _mock_client(data: dict) -> MagicMock:
    client = MagicMock()
    client.get_stock_bars.return_value.data = data
    return client


class TestFetchAtrForTickers:
    def _call(self, tickers, client_data):
        from orchestrator import _fetch_atr_for_tickers
        with patch("agents.alpaca_broker._dclient", return_value=_mock_client(client_data)):
            return _fetch_atr_for_tickers(tickers)

    def test_empty_tickers_returns_empty(self):
        from orchestrator import _fetch_atr_for_tickers
        assert _fetch_atr_for_tickers([]) == {}

    def test_single_ticker_returns_atr_pct(self):
        price   = 100.0
        atr_abs = 2.0   # 2% of 100
        data    = {"AAPL": _bars_for_price(price=price, n=20, atr_abs=atr_abs)}
        result  = self._call(["AAPL"], data)
        assert "AAPL" in result
        assert result["AAPL"] is not None
        assert 1.0 < result["AAPL"] < 6.0  # broad sanity check around 2×2=4% (TR includes prev_c spread)

    def test_two_tickers(self):
        data = {
            "AAPL": _bars_for_price(price=150.0, n=20, atr_abs=3.0),
            "MSFT": _bars_for_price(price=300.0, n=20, atr_abs=6.0),
        }
        result = self._call(["AAPL", "MSFT"], data)
        assert result["AAPL"] is not None
        assert result["MSFT"] is not None
        # MSFT ATR% = AAPL ATR% since both are 2% of price
        assert abs(result["AAPL"] - result["MSFT"]) < 0.5

    def test_too_few_bars_returns_none(self):
        data = {"AAPL": _bars_for_price(n=5)}  # only 5 bars, threshold is 10
        result = self._call(["AAPL"], data)
        assert result["AAPL"] is None

    def test_missing_ticker_in_response_returns_none(self):
        result = self._call(["AAPL"], {})   # Alpaca returned no data for AAPL
        assert result["AAPL"] is None

    def test_alpaca_exception_returns_all_none(self):
        from orchestrator import _fetch_atr_for_tickers
        broken_client = MagicMock()
        broken_client.get_stock_bars.side_effect = RuntimeError("network error")
        with patch("agents.alpaca_broker._dclient", return_value=broken_client):
            result = _fetch_atr_for_tickers(["AAPL", "MSFT"])
        assert result == {"AAPL": None, "MSFT": None}

    def test_atr_pct_rounds_to_two_decimal_places(self):
        data   = {"AAPL": _bars_for_price(price=100.0, n=20, atr_abs=2.0)}
        result = self._call(["AAPL"], data)
        val    = result["AAPL"]
        assert val == round(val, 2)
