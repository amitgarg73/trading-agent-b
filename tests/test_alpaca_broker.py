"""
Tests for agents/alpaca_broker.py — MAE/MFE computation and watermark tracking.
Broker network calls are fully mocked.
"""
import pytest
from unittest.mock import patch, MagicMock, call
from agents.alpaca_broker import _close_position, update_positions_intraday


def _pos(entry=100.0, fill=100.0, shares=10, high_wm=None, low_wm=None, cur=100.0) -> dict:
    return {
        "id":             "pos-1",
        "ticker":         "AAPL",
        "shares":         shares,
        "entry_price":    entry,
        "fill_price":     fill,
        "target_price":   110.0,
        "stop_loss":      95.0,
        "high_watermark": high_wm if high_wm is not None else fill,
        "low_watermark":  low_wm  if low_wm  is not None else fill,
        "current_price":  cur,
        "unrealized_pnl": shares * (cur - entry),
        "status":         "OPEN",
    }


def _filled_order(close_price: float) -> MagicMock:
    """Return a mock Alpaca order that looks like a confirmed fill."""
    o = MagicMock()
    o.status = "filled"
    o.filled_avg_price = close_price
    return o


def _alpaca_mock_with_open(ticker: str = "AAPL") -> MagicMock:
    """
    Return a mock _get() return value where `ticker` exists as an open Alpaca position.
    This makes _reconcile_with_alpaca() skip the position (no ghost-position action).
    """
    pos_mock = MagicMock()
    pos_mock.symbol = ticker
    client = MagicMock()
    client.get_all_positions.return_value = [pos_mock]
    return client


# ── MAE / MFE tests ──────────────────────────────────────────────────────────

@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker._get")
def test_close_position_writes_mfe(mock_get, mock_update):
    mock_get.return_value.submit_order.return_value = MagicMock()
    mock_get.return_value.get_order_by_id.return_value = _filled_order(107.0)
    pos = _pos(fill=100.0, shares=10, high_wm=108.0, low_wm=100.0)
    _close_position(pos, price=107.0, reason="TARGET")

    update_kwargs = mock_update.call_args[0][2]
    assert update_kwargs["mfe"] == round((108.0 - 100.0) * 10, 2)   # $80
    assert update_kwargs["mae"] == 0.0                               # never went against us


@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker._get")
def test_close_position_writes_mae(mock_get, mock_update):
    mock_get.return_value.submit_order.return_value = MagicMock()
    mock_get.return_value.get_order_by_id.return_value = _filled_order(97.0)
    pos = _pos(fill=100.0, shares=10, high_wm=100.0, low_wm=96.0)
    _close_position(pos, price=97.0, reason="STOP")

    update_kwargs = mock_update.call_args[0][2]
    assert update_kwargs["mae"] == round((100.0 - 96.0) * 10, 2)   # $40
    assert update_kwargs["mfe"] == 0.0


@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker._get")
def test_pnl_uses_fill_price_not_entry(mock_get, mock_update):
    """P&L should be computed from actual fill, not planned entry price."""
    mock_get.return_value.submit_order.return_value = MagicMock()
    mock_get.return_value.get_order_by_id.return_value = _filled_order(110.0)
    # Planned entry 100, actual fill 100.5 (5 bps slip), close at 110
    pos = _pos(entry=100.0, fill=100.5, shares=10, high_wm=110.0, low_wm=100.5)
    _close_position(pos, price=110.0, reason="TARGET")

    update_kwargs = mock_update.call_args[0][2]
    expected_pnl = round(10 * (110.0 - 100.5), 2)   # $95, not $100
    assert update_kwargs["realized_pnl"] == expected_pnl


# ── Low watermark tracking in intraday loop ───────────────────────────────────

@patch("agents.alpaca_broker._get")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_low_watermark_updates_when_price_drops(mock_price, mock_update, mock_select, mock_get):
    # AAPL exists in Alpaca → reconciliation skips it
    mock_get.return_value = _alpaca_mock_with_open("AAPL")
    pos = _pos(fill=100.0, shares=10, high_wm=100.0, low_wm=100.0)
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 97.5   # price drops — trailing stop may fire

    update_positions_intraday()

    # First db.update call is always the watermark update — check it
    first_update_kwargs = mock_update.call_args_list[0][0][2]
    assert first_update_kwargs["low_watermark"] == 97.5


@patch("agents.alpaca_broker._get")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_high_watermark_updates_when_price_rises(mock_price, mock_update, mock_select, mock_get):
    # AAPL exists in Alpaca → reconciliation skips it
    mock_get.return_value = _alpaca_mock_with_open("AAPL")
    pos = _pos(fill=100.0, shares=10, high_wm=100.0, low_wm=100.0)
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 105.0

    update_positions_intraday()

    update_kwargs = mock_update.call_args[0][2]
    assert update_kwargs["high_watermark"] == 105.0


# ── Reconciliation: stop_limit mechanism classification ──────────────────────

@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker._get")
def test_reconcile_stop_limit_classified_as_stop(mock_get, mock_select, mock_update):
    """stop_limit order_type must be classified as STOP, not TARGET."""
    from agents.alpaca_broker import _reconcile_with_alpaca
    from alpaca.trading.requests import GetOrdersRequest

    sell_order = MagicMock()
    sell_order.symbol = "AAPL"
    sell_order.side = "sell"
    sell_order.status = "filled"
    sell_order.filled_avg_price = 95.0
    sell_order.order_type = "stop_limit"
    sell_order.filled_at = "2026-05-21T15:30:00"
    sell_order.submitted_at = "2026-05-21T15:30:00"

    pos = _pos(fill=100.0, shares=10)
    pos["ticker"] = "AAPL"
    pos["status"] = "OPEN"

    mock_get.return_value.get_all_positions.return_value = []   # not open in Alpaca
    mock_get.return_value.get_orders.return_value = [sell_order]
    mock_select.return_value = [pos]

    _reconcile_with_alpaca()

    update_kwargs = mock_update.call_args[0][2]
    assert update_kwargs["close_reason"] == "STOP"
    assert update_kwargs["exit_mechanism"] == "STOP"
    assert update_kwargs["realized_pnl"] == round((95.0 - 100.0) * 10, 2)   # -$50


@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker._get")
def test_reconcile_limit_classified_as_target(mock_get, mock_select, mock_update):
    """Pure limit sell = take-profit hit → TARGET."""
    from agents.alpaca_broker import _reconcile_with_alpaca

    sell_order = MagicMock()
    sell_order.symbol = "AAPL"
    sell_order.side = "sell"
    sell_order.status = "filled"
    sell_order.filled_avg_price = 110.0
    sell_order.order_type = "limit"
    sell_order.filled_at = "2026-05-21T15:30:00"
    sell_order.submitted_at = "2026-05-21T15:30:00"

    pos = _pos(fill=100.0, shares=10)
    pos["ticker"] = "AAPL"
    pos["status"] = "OPEN"

    mock_get.return_value.get_all_positions.return_value = []
    mock_get.return_value.get_orders.return_value = [sell_order]
    mock_select.return_value = [pos]

    _reconcile_with_alpaca()

    update_kwargs = mock_update.call_args[0][2]
    assert update_kwargs["close_reason"] == "TARGET"
    assert update_kwargs["exit_mechanism"] == "TARGET"
    assert update_kwargs["realized_pnl"] == round((110.0 - 100.0) * 10, 2)   # +$100


@patch("agents.alpaca_broker._get")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_low_watermark_never_rises(mock_price, mock_update, mock_select, mock_get):
    """Low watermark should not increase even if price later recovers."""
    # AAPL exists in Alpaca → reconciliation skips it
    mock_get.return_value = _alpaca_mock_with_open("AAPL")
    pos = _pos(fill=100.0, shares=10, high_wm=100.0, low_wm=96.0)
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 102.0   # recovered above prior low

    update_positions_intraday()

    update_kwargs = mock_update.call_args[0][2]
    assert update_kwargs["low_watermark"] == 96.0   # stays at the prior low
