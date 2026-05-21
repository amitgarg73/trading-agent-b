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

@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker._get")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_low_watermark_updates_when_price_drops(mock_price, mock_update, mock_select, mock_get, mock_signals):
    # AAPL exists in Alpaca → reconciliation skips it
    mock_get.return_value = _alpaca_mock_with_open("AAPL")
    pos = _pos(fill=100.0, shares=10, high_wm=100.0, low_wm=100.0)
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 97.5   # price drops — trailing stop may fire

    update_positions_intraday()

    # First db.update call is always the watermark update — check it
    first_update_kwargs = mock_update.call_args_list[0][0][2]
    assert first_update_kwargs["low_watermark"] == 97.5


@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker._get")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_high_watermark_updates_when_price_rises(mock_price, mock_update, mock_select, mock_get, mock_signals):
    # AAPL exists in Alpaca → reconciliation skips it
    mock_get.return_value = _alpaca_mock_with_open("AAPL")
    pos = _pos(fill=100.0, shares=10, high_wm=100.0, low_wm=100.0)
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 103.0   # below +1R (105) so R-ladder stays quiet

    update_positions_intraday()

    update_kwargs = mock_update.call_args[0][2]
    assert update_kwargs["high_watermark"] == 103.0


# ── Reconciliation: UNFILLED detection ───────────────────────────────────────

def _buy_order(symbol, status, submitted_at="2026-05-21T15:00:00"):
    o = MagicMock()
    o.symbol = symbol
    o.side = "buy"
    o.status = status
    o.filled_at = submitted_at
    o.submitted_at = submitted_at
    return o


@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker._get")
def test_reconcile_filled_buy_not_marked_unfilled(mock_get, mock_select, mock_update):
    """Entry filled (buy=filled) but gone from Alpaca → NOT UNFILLED.
    update_positions_intraday() resolves the exit via manual trail/stop logic."""
    from agents.alpaca_broker import _reconcile_with_alpaca

    pos = _pos(fill=100.0, shares=10)
    pos["ticker"] = "AAPL"
    pos["status"] = "OPEN"

    mock_get.return_value.get_all_positions.return_value = []
    mock_get.return_value.get_orders.return_value = [_buy_order("AAPL", status="filled")]
    mock_select.return_value = [pos]

    _reconcile_with_alpaca()

    mock_update.assert_not_called()


@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker._get")
def test_reconcile_pending_buy_not_marked_unfilled(mock_get, mock_select, mock_update):
    """Buy order in flight → leave OPEN, don't mark UNFILLED."""
    from agents.alpaca_broker import _reconcile_with_alpaca

    pos = _pos(fill=100.0, shares=10)
    pos["ticker"] = "TSLA"
    pos["status"] = "OPEN"

    mock_get.return_value.get_all_positions.return_value = []
    mock_get.return_value.get_orders.return_value = [_buy_order("TSLA", status="accepted")]
    mock_select.return_value = [pos]

    _reconcile_with_alpaca()

    mock_update.assert_not_called()


@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker._get")
def test_reconcile_no_buy_marked_unfilled(mock_get, mock_select, mock_update):
    """No buy order at all → entry never executed → UNFILLED."""
    from agents.alpaca_broker import _reconcile_with_alpaca

    pos = _pos(fill=100.0, shares=10)
    pos["ticker"] = "NVDA"
    pos["status"] = "OPEN"

    mock_get.return_value.get_all_positions.return_value = []
    mock_get.return_value.get_orders.return_value = []
    mock_select.return_value = [pos]

    _reconcile_with_alpaca()

    mock_update.assert_called_once()
    kwargs = mock_update.call_args[0][2]
    assert kwargs["close_reason"] == "UNFILLED"
    assert kwargs["exit_mechanism"] == "UNFILLED"
    assert kwargs["realized_pnl"] == 0


@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker._get")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_low_watermark_never_rises(mock_price, mock_update, mock_select, mock_get, mock_signals):
    """Low watermark should not increase even if price later recovers."""
    # AAPL exists in Alpaca → reconciliation skips it
    mock_get.return_value = _alpaca_mock_with_open("AAPL")
    pos = _pos(fill=100.0, shares=10, high_wm=100.0, low_wm=96.0)
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 102.0   # recovered above prior low

    update_positions_intraday()

    update_kwargs = mock_update.call_args[0][2]
    assert update_kwargs["low_watermark"] == 96.0   # stays at the prior low


# ── R-multiple stop ladder tests ──────────────────────────────────────────────

@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker._get")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_r_ladder_moves_stop_to_breakeven_at_1R(mock_price, mock_update, mock_select, mock_get, mock_signals):
    """At +1R profit (price = entry + R), stop should ratchet to entry (breakeven)."""
    mock_get.return_value = _alpaca_mock_with_open("AAPL")
    # entry=100, stop=95 → R=5; +1R threshold = entry+R = 105
    pos = _pos(entry=100.0, fill=100.0, shares=10, high_wm=100.0, low_wm=100.0)
    pos["target_price"] = 120.0   # keep target out of range
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 105.0

    update_positions_intraday()

    first_kwargs = mock_update.call_args_list[0][0][2]
    assert first_kwargs.get("stop_loss") == 100.0   # breakeven


@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker._get")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_r_ladder_moves_stop_to_entry_plus_r_at_2R(mock_price, mock_update, mock_select, mock_get, mock_signals):
    """At +2R profit (price = entry + 2R), stop should ratchet to entry + R."""
    mock_get.return_value = _alpaca_mock_with_open("AAPL")
    pos = _pos(entry=100.0, fill=100.0, shares=10, high_wm=100.0, low_wm=100.0)
    pos["target_price"] = 120.0
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 110.0   # +2R (entry + 2*5 = 110)

    update_positions_intraday()

    first_kwargs = mock_update.call_args_list[0][0][2]
    assert first_kwargs.get("stop_loss") == 105.0   # entry + R


@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker._get")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_r_ladder_no_change_below_1R(mock_price, mock_update, mock_select, mock_get, mock_signals):
    """Below +1R, stop must not be adjusted."""
    mock_get.return_value = _alpaca_mock_with_open("AAPL")
    pos = _pos(entry=100.0, fill=100.0, shares=10, high_wm=100.0, low_wm=100.0)
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 103.0   # below +1R threshold of 105

    update_positions_intraday()

    for c in mock_update.call_args_list:
        assert "stop_loss" not in c[0][2]


# ── VWAP exit tests ────────────────────────────────────────────────────────────

@patch("agents.alpaca_broker._close_position")
@patch("agents.alpaca_broker.get_intraday_signals")
@patch("agents.alpaca_broker._get")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_vwap_exit_when_capital_at_risk(mock_price, mock_update, mock_select, mock_get, mock_signals, mock_close):
    """Price below VWAP with stop < entry should trigger VWAP_BREAK close."""
    mock_get.return_value = _alpaca_mock_with_open("AAPL")
    pos = _pos(entry=100.0, fill=100.0, shares=10, high_wm=100.0, low_wm=100.0)
    pos["stop_loss"] = 95.0   # stop < entry → capital at risk
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 98.0
    mock_signals.return_value = {"AAPL": {"vwap": 99.0}}   # price(98) < vwap(99)

    update_positions_intraday()

    mock_close.assert_called_once()
    assert mock_close.call_args[0][2] == "VWAP_BREAK"


@patch("agents.alpaca_broker._close_position")
@patch("agents.alpaca_broker.get_intraday_signals")
@patch("agents.alpaca_broker._get")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_vwap_exit_skipped_when_r_ladder_protected(mock_price, mock_update, mock_select, mock_get, mock_signals, mock_close):
    """VWAP exit must not fire when stop >= entry (R-ladder has protected capital)."""
    mock_get.return_value = _alpaca_mock_with_open("AAPL")
    pos = _pos(entry=100.0, fill=100.0, shares=10, high_wm=100.0, low_wm=100.0)
    pos["stop_loss"] = 100.0   # stop == entry → R-ladder already moved stop to breakeven
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 101.0   # above stop, below target — no exit triggers
    mock_signals.return_value = {"AAPL": {"vwap": 102.0}}   # price(101) < vwap(102), but should be ignored

    update_positions_intraday()

    mock_close.assert_not_called()
