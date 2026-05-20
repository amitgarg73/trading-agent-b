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


# ── MAE / MFE tests ──────────────────────────────────────────────────────────

@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker._get")
def test_close_position_writes_mfe(mock_get, mock_update):
    mock_get.return_value.submit_order.return_value = MagicMock()
    pos = _pos(fill=100.0, shares=10, high_wm=108.0, low_wm=100.0)
    _close_position(pos, price=107.0, reason="TARGET")

    update_kwargs = mock_update.call_args[0][2]
    assert update_kwargs["mfe"] == round((108.0 - 100.0) * 10, 2)   # $80
    assert update_kwargs["mae"] == 0.0                               # never went against us


@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker._get")
def test_close_position_writes_mae(mock_get, mock_update):
    mock_get.return_value.submit_order.return_value = MagicMock()
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
    mock_get.return_value.submit_order.return_value = MagicMock()
    pos = _pos(fill=100.0, shares=10, high_wm=100.0, low_wm=100.0)
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 97.5   # price drops — trailing stop may fire

    update_positions_intraday()

    # First db.update call is always the watermark update — check it
    first_update_kwargs = mock_update.call_args_list[0][0][2]
    assert first_update_kwargs["low_watermark"] == 97.5


@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_high_watermark_updates_when_price_rises(mock_price, mock_update, mock_select):
    pos = _pos(fill=100.0, shares=10, high_wm=100.0, low_wm=100.0)
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 105.0

    update_positions_intraday()

    update_kwargs = mock_update.call_args[0][2]
    assert update_kwargs["high_watermark"] == 105.0


@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_low_watermark_never_rises(mock_price, mock_update, mock_select):
    """Low watermark should not increase even if price later recovers."""
    pos = _pos(fill=100.0, shares=10, high_wm=100.0, low_wm=96.0)
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 102.0   # recovered above prior low

    update_positions_intraday()

    update_kwargs = mock_update.call_args[0][2]
    assert update_kwargs["low_watermark"] == 96.0   # stays at the prior low
