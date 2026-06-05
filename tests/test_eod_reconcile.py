"""
Tests for reconcile_eod_stale_opens() in agents/alpaca_broker.py.
All Alpaca and DB calls are mocked.
"""
import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone

from agents.alpaca_broker import reconcile_eod_stale_opens


def _pos(ticker="AAPL", opened_at="2026-06-04T14:30:00", fill_price=150.0, shares=10):
    return {
        "id":          f"pos-{ticker}",
        "ticker":      ticker,
        "shares":      shares,
        "entry_price": fill_price,
        "fill_price":  fill_price,
        "status":      "OPEN",
        "opened_at":   opened_at,
    }


def _sell_order(symbol, filled_avg_price, filled_at="2026-06-04T19:55:00+00:00"):
    o = MagicMock()
    o.symbol = symbol
    o.side = MagicMock()
    o.side.value = "sell"
    o.status = MagicMock()
    o.status.value = "filled"
    o.filled_avg_price = filled_avg_price
    o.filled_at = filled_at
    o.submitted_at = filled_at
    o.order_type = "market"
    return o


class TestReconcileEodStaleOpens:

    def test_no_open_positions_returns_empty(self):
        with patch("agents.alpaca_broker.open_positions", return_value=[]), \
             patch("agents.alpaca_broker.time") as mock_time:
            result = reconcile_eod_stale_opens(wait_s=0)
            assert result == []
            mock_time.sleep.assert_called_once_with(0)

    def test_all_positions_still_in_alpaca_returns_empty(self):
        positions = [_pos("AAPL"), _pos("MSFT")]
        with patch("agents.alpaca_broker.open_positions", return_value=positions), \
             patch("agents.alpaca_broker.get_open_tickers", return_value={"AAPL", "MSFT"}), \
             patch("agents.alpaca_broker.time"):
            result = reconcile_eod_stale_opens(wait_s=0)
            assert result == []

    def test_stale_position_reconciled_with_fill_price(self):
        pos = _pos("GOOGL", fill_price=180.0, shares=5)
        sell_ord = _sell_order("GOOGL", filled_avg_price=182.0)

        mock_broker = MagicMock()
        mock_broker.get_orders.return_value = [sell_ord]

        with patch("agents.alpaca_broker.open_positions", return_value=[pos]), \
             patch("agents.alpaca_broker.get_open_tickers", return_value=set()), \
             patch("agents.alpaca_broker._get", return_value=mock_broker), \
             patch("agents.alpaca_broker.db") as mock_db, \
             patch("agents.alpaca_broker.time"):
            result = reconcile_eod_stale_opens(wait_s=0)

        assert result == ["GOOGL"]
        mock_db.update.assert_called_once()
        update_payload = mock_db.update.call_args[0][2]
        assert update_payload["status"] == "CLOSED"
        assert update_payload["close_price"] == 182.0
        assert update_payload["realized_pnl"] == round(5 * (182.0 - 180.0), 2)
        assert update_payload["close_reason"] == "EOD"

    def test_multiple_stale_positions_all_reconciled(self):
        positions = [
            _pos("GOOGL", fill_price=180.0, shares=5),
            _pos("ORCL",  fill_price=100.0, shares=8),
            _pos("JPM",   fill_price=200.0, shares=3),
        ]
        orders = [
            _sell_order("GOOGL", 181.0),
            _sell_order("ORCL",   99.0),
            _sell_order("JPM",   202.0),
        ]
        mock_broker = MagicMock()
        mock_broker.get_orders.return_value = orders

        with patch("agents.alpaca_broker.open_positions", return_value=positions), \
             patch("agents.alpaca_broker.get_open_tickers", return_value=set()), \
             patch("agents.alpaca_broker._get", return_value=mock_broker), \
             patch("agents.alpaca_broker.db") as mock_db, \
             patch("agents.alpaca_broker.time"):
            result = reconcile_eod_stale_opens(wait_s=0)

        assert sorted(result) == ["GOOGL", "JPM", "ORCL"]
        assert mock_db.update.call_count == 3

    def test_no_sell_order_found_leaves_position_open(self):
        pos = _pos("AAPL", fill_price=150.0)
        mock_broker = MagicMock()
        mock_broker.get_orders.return_value = []  # no orders at all

        with patch("agents.alpaca_broker.open_positions", return_value=[pos]), \
             patch("agents.alpaca_broker.get_open_tickers", return_value=set()), \
             patch("agents.alpaca_broker._get", return_value=mock_broker), \
             patch("agents.alpaca_broker.db") as mock_db, \
             patch("agents.alpaca_broker.time"):
            result = reconcile_eod_stale_opens(wait_s=0)

        assert result == []
        mock_db.update.assert_not_called()

    def test_sell_order_before_position_open_ignored(self):
        """Fill that predates the position open should not be used."""
        pos = _pos("AAPL", opened_at="2026-06-04T15:00:00", fill_price=150.0)
        # Sell order filled before position opened
        early_sell = _sell_order("AAPL", 155.0, filled_at="2026-06-04T14:00:00+00:00")
        mock_broker = MagicMock()
        mock_broker.get_orders.return_value = [early_sell]

        with patch("agents.alpaca_broker.open_positions", return_value=[pos]), \
             patch("agents.alpaca_broker.get_open_tickers", return_value=set()), \
             patch("agents.alpaca_broker._get", return_value=mock_broker), \
             patch("agents.alpaca_broker.db") as mock_db, \
             patch("agents.alpaca_broker.time"):
            result = reconcile_eod_stale_opens(wait_s=0)

        assert result == []
        mock_db.update.assert_not_called()

    def test_alpaca_position_fetch_failure_returns_empty(self):
        pos = _pos("AAPL")
        with patch("agents.alpaca_broker.open_positions", return_value=[pos]), \
             patch("agents.alpaca_broker.get_open_tickers", side_effect=Exception("network error")), \
             patch("agents.alpaca_broker.db") as mock_db, \
             patch("agents.alpaca_broker.time"):
            result = reconcile_eod_stale_opens(wait_s=0)

        assert result == []
        mock_db.update.assert_not_called()

    def test_waits_specified_seconds_before_reconciling(self):
        with patch("agents.alpaca_broker.open_positions", return_value=[]), \
             patch("agents.alpaca_broker.time") as mock_time:
            reconcile_eod_stale_opens(wait_s=30)
            mock_time.sleep.assert_called_once_with(30)
