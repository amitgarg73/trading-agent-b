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


def _alpaca_mock_with_open(ticker: str = "AAPL", qty: int = 10) -> MagicMock:
    """
    Return a mock _get() return value where `ticker` exists as an open Alpaca position.
    This makes _reconcile_with_alpaca() skip the position (no ghost-position action).
    qty must match the test position's shares so qty_sync doesn't fire unexpectedly.
    """
    pos_mock = MagicMock()
    pos_mock.symbol = ticker
    pos_mock.qty = str(qty)
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

    # Backfill trail may add an update before the watermark update — find it by content
    watermark_calls = [c for c in mock_update.call_args_list
                       if "low_watermark" in c[0][2]]
    assert watermark_calls, "Expected a db.update call with low_watermark"
    assert watermark_calls[0][0][2]["low_watermark"] == 97.5


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

    watermark_calls = [c for c in mock_update.call_args_list
                       if "high_watermark" in c[0][2]]
    assert watermark_calls, "Expected a db.update call with high_watermark"
    assert watermark_calls[0][0][2]["high_watermark"] == 103.0


# ── Reconciliation: UNFILLED detection ───────────────────────────────────────

def _buy_order(symbol, status, submitted_at=None):
    from datetime import datetime
    ts = submitted_at or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    o = MagicMock()
    o.symbol = symbol
    o.side = "buy"
    o.status = status
    o.filled_at = ts
    o.submitted_at = ts
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

    watermark_calls = [c for c in mock_update.call_args_list if "low_watermark" in c[0][2]]
    assert watermark_calls, "Expected a db.update with low_watermark"
    assert watermark_calls[0][0][2]["low_watermark"] == 96.0   # stays at the prior low


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

    stop_calls = [c for c in mock_update.call_args_list if "stop_loss" in c[0][2]]
    assert stop_calls, "Expected a db.update with stop_loss for +1R breakeven"
    assert stop_calls[0][0][2]["stop_loss"] == 100.0   # breakeven


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

    stop_calls = [c for c in mock_update.call_args_list if "stop_loss" in c[0][2]]
    assert stop_calls, "Expected a db.update with stop_loss for +2R ratchet"
    assert stop_calls[0][0][2]["stop_loss"] == 105.0   # entry + R


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


# ── Native trail: get_order_fill ─────────────────────────────────────────────

def _bracket_order_with_leg(order_type: str, status: str, fill_price: float) -> MagicMock:
    """Return a mock parent bracket order with one filled child leg."""
    leg = MagicMock()
    leg.status = status
    leg.order_type = order_type
    leg.filled_avg_price = fill_price
    order = MagicMock()
    order.legs = [leg]
    return order


@patch("agents.alpaca_broker._get")
def test_get_order_fill_native_trail(mock_get):
    from agents.alpaca_broker import get_order_fill
    mock_get.return_value.get_order_by_id.return_value = _bracket_order_with_leg(
        "trailing_stop", "filled", 105.0
    )
    price, mechanism = get_order_fill("order-123")
    assert price == 105.0
    assert mechanism == "NATIVE_TRAIL"


@patch("agents.alpaca_broker._get")
def test_get_order_fill_target(mock_get):
    from agents.alpaca_broker import get_order_fill
    mock_get.return_value.get_order_by_id.return_value = _bracket_order_with_leg(
        "limit", "filled", 112.5
    )
    price, mechanism = get_order_fill("order-456")
    assert price == 112.5
    assert mechanism == "TARGET"


@patch("agents.alpaca_broker._get")
def test_get_order_fill_stop(mock_get):
    from agents.alpaca_broker import get_order_fill
    mock_get.return_value.get_order_by_id.return_value = _bracket_order_with_leg(
        "stop", "filled", 96.0
    )
    price, mechanism = get_order_fill("order-789")
    assert price == 96.0
    assert mechanism == "STOP"


@patch("agents.alpaca_broker._get")
def test_get_order_fill_no_filled_leg_returns_none(mock_get):
    """If no leg is filled yet, return (None, None)."""
    from agents.alpaca_broker import get_order_fill
    mock_get.return_value.get_order_by_id.return_value = _bracket_order_with_leg(
        "trailing_stop", "pending_new", 0.0
    )
    price, mechanism = get_order_fill("order-000")
    assert price is None
    assert mechanism is None


# ── Native trail: reconcile resolves bracket exits ───────────────────────────

@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker._get")
def test_reconcile_bracket_exit_closed_in_db(mock_get, mock_select, mock_update):
    """When bracket fires (in filled_buys, gone from Alpaca), reconcile closes the DB row."""
    from agents.alpaca_broker import _reconcile_with_alpaca

    pos = _pos(fill=100.0, shares=10)
    pos["ticker"] = "AAPL"
    pos["status"] = "OPEN"
    pos["alpaca_order_id"] = "parent-order-abc"

    mock_get.return_value.get_all_positions.return_value = []  # gone from Alpaca
    mock_get.return_value.get_orders.return_value = [_buy_order("AAPL", status="filled")]
    mock_get.return_value.get_order_by_id.return_value = _bracket_order_with_leg(
        "trailing_stop", "filled", 105.0
    )
    mock_select.return_value = [pos]

    _reconcile_with_alpaca()

    mock_update.assert_called_once()
    kwargs = mock_update.call_args[0][2]
    assert kwargs["status"] == "CLOSED"
    assert kwargs["close_reason"] == "NATIVE_TRAIL"
    assert kwargs["exit_mechanism"] == "NATIVE_TRAIL"
    assert kwargs["close_price"] == 105.0
    assert kwargs["realized_pnl"] == pytest.approx((105.0 - 100.0) * 10)


@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker._get")
def test_reconcile_bracket_exit_no_filled_leg_skips_update(mock_get, mock_select, mock_update):
    """If get_order_fill returns None (leg not filled yet), don't close the DB row."""
    from agents.alpaca_broker import _reconcile_with_alpaca

    pos = _pos(fill=100.0, shares=10)
    pos["ticker"] = "AAPL"
    pos["status"] = "OPEN"
    pos["alpaca_order_id"] = "parent-order-abc"

    mock_get.return_value.get_all_positions.return_value = []
    mock_get.return_value.get_orders.return_value = [_buy_order("AAPL", status="filled")]
    # Leg still pending — not filled yet
    mock_get.return_value.get_order_by_id.return_value = _bracket_order_with_leg(
        "trailing_stop", "pending_new", 0.0
    )
    mock_select.return_value = [pos]

    _reconcile_with_alpaca()

    mock_update.assert_not_called()


@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker._get")
def test_reconcile_market_sell_recovery(mock_get, mock_select, mock_update):
    """When bracket legs didn't fire but a market sell closed the position,
    reconcile must close the DB row using the market sell's fill price."""
    from datetime import datetime
    from agents.alpaca_broker import _reconcile_with_alpaca

    pos = _pos(fill=100.0, shares=10)
    pos["ticker"]          = "AAPL"
    pos["status"]          = "OPEN"
    pos["alpaca_order_id"] = "parent-order-abc"
    pos["opened_at"]       = "2026-06-02T14:00:00"

    # Build order list: filled buy (entry) + market sell (close) + no trailing sell
    buy_order = _buy_order("AAPL", status="filled", submitted_at="2026-06-02T14:00:01")

    sell_order = MagicMock()
    sell_order.symbol         = "AAPL"
    sell_order.side           = "sell"
    sell_order.order_type     = "market"
    sell_order.status         = "filled"
    sell_order.filled_avg_price = 97.5
    sell_order.filled_at      = "2026-06-02T15:00:00"
    sell_order.submitted_at   = "2026-06-02T15:00:00"

    mock_get.return_value.get_all_positions.return_value = []
    mock_get.return_value.get_orders.return_value = [buy_order, sell_order]
    # Bracket legs not filled
    mock_get.return_value.get_order_by_id.return_value = _bracket_order_with_leg(
        "stop", "canceled", 0.0
    )
    mock_select.return_value = [pos]

    _reconcile_with_alpaca()

    mock_update.assert_called_once()
    kwargs = mock_update.call_args[0][2]
    assert kwargs["status"]         == "CLOSED"
    assert kwargs["close_reason"]   == "MANUAL_CLOSE"
    assert kwargs["close_price"]    == pytest.approx(97.5)
    assert kwargs["realized_pnl"]   == pytest.approx((97.5 - 100.0) * 10)


@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker._get")
def test_reconcile_market_sell_ignored_before_entry(mock_get, mock_select, mock_update):
    """A market sell that predates the position open must not be used as close price."""
    from agents.alpaca_broker import _reconcile_with_alpaca

    pos = _pos(fill=100.0, shares=10)
    pos["ticker"]          = "AAPL"
    pos["status"]          = "OPEN"
    pos["alpaca_order_id"] = "parent-order-abc"
    pos["opened_at"]       = "2026-06-02T15:00:00"

    buy_order = _buy_order("AAPL", status="filled", submitted_at="2026-06-02T15:00:01")

    # Sell from a prior trade — earlier than opened_at
    old_sell = MagicMock()
    old_sell.symbol           = "AAPL"
    old_sell.side             = "sell"
    old_sell.order_type       = "market"
    old_sell.status           = "filled"
    old_sell.filled_avg_price = 90.0
    old_sell.filled_at        = "2026-06-02T14:00:00"  # before opened_at
    old_sell.submitted_at     = "2026-06-02T14:00:00"

    mock_get.return_value.get_all_positions.return_value = []
    mock_get.return_value.get_orders.return_value = [buy_order, old_sell]
    mock_get.return_value.get_order_by_id.return_value = _bracket_order_with_leg(
        "stop", "canceled", 0.0
    )
    mock_select.return_value = [pos]

    _reconcile_with_alpaca()

    # Old sell must not trigger a close — no db.update
    mock_update.assert_not_called()


@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker._get")
def test_reconcile_datetime_filled_at_not_string(mock_get, mock_select, mock_update):
    """filled_at is a datetime object — str() wrap must prevent AttributeError."""
    from datetime import datetime
    from agents.alpaca_broker import _reconcile_with_alpaca

    pos = _pos(fill=100.0, shares=10)
    pos["ticker"] = "AAPL"
    pos["status"] = "OPEN"

    o = MagicMock()
    o.symbol = "AAPL"
    o.side = "buy"
    o.status = "filled"
    o.filled_at    = datetime.utcnow()   # datetime object, not string
    o.submitted_at = datetime.utcnow()

    mock_get.return_value.get_all_positions.return_value = []
    mock_get.return_value.get_orders.return_value = [o]
    mock_select.return_value = [pos]

    _reconcile_with_alpaca()   # must not raise AttributeError


@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker._get")
def test_reconcile_filled_buy_not_marked_unfilled(mock_get, mock_select, mock_update):
    """Entry filled (buy=filled) — must never be marked UNFILLED regardless of bracket state."""
    from agents.alpaca_broker import _reconcile_with_alpaca

    pos = _pos(fill=100.0, shares=10)
    pos["ticker"] = "AAPL"
    pos["status"] = "OPEN"
    # No alpaca_order_id → get_order_fill skipped; position stays open for intraday loop

    mock_get.return_value.get_all_positions.return_value = []
    mock_get.return_value.get_orders.return_value = [_buy_order("AAPL", status="filled")]
    mock_select.return_value = [pos]

    _reconcile_with_alpaca()

    # Must not be marked UNFILLED
    for call_args in mock_update.call_args_list:
        kwargs = call_args[0][2] if call_args[0] else call_args[1]
        assert kwargs.get("close_reason") != "UNFILLED"


# ── Native trail: cancel bracket before manual close ─────────────────────────

@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker._get")
def test_close_position_cancels_bracket_before_sell(mock_get, mock_update):
    """_close_position must cancel the bracket order before submitting the market sell."""
    mock_get.return_value.submit_order.return_value = MagicMock()
    mock_get.return_value.get_order_by_id.return_value = _filled_order(107.0)

    pos = _pos(fill=100.0, shares=10, high_wm=107.0, low_wm=100.0)
    pos["alpaca_order_id"] = "bracket-parent-id"

    _close_position(pos, price=107.0, reason="VWAP_BREAK")

    mock_get.return_value.cancel_order_by_id.assert_called_once_with("bracket-parent-id")


@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker._get")
def test_close_position_no_order_id_skips_cancel(mock_get, mock_update):
    """If no alpaca_order_id on position, cancel is not attempted."""
    mock_get.return_value.submit_order.return_value = MagicMock()
    mock_get.return_value.get_order_by_id.return_value = _filled_order(107.0)

    pos = _pos(fill=100.0, shares=10)  # no alpaca_order_id

    _close_position(pos, price=107.0, reason="EOD")

    mock_get.return_value.cancel_order_by_id.assert_not_called()


# ── place_orders uses stop_price not trail_percent ────────────────────────────

@patch("agents.alpaca_broker.get_live_quotes")
@patch("agents.alpaca_broker._get")
def test_place_orders_uses_limit_order_with_stop_price(mock_get, mock_quotes):
    """place_orders must use LimitOrderRequest + StopLossRequest(stop_price) — not market order or trail_percent."""
    from unittest.mock import MagicMock
    from alpaca.trading.requests import LimitOrderRequest, StopLossRequest
    import agents.alpaca_broker as broker_mod

    mock_quotes.return_value = {"AAPL": {"ask": 150.10, "bid": 150.00}}  # tight spread → bid

    filled = MagicMock()
    filled.id = "ord-123"
    filled.status = "filled"
    filled.filled_avg_price = 150.05

    mock_broker = mock_get.return_value
    mock_broker.submit_order.return_value = MagicMock(id="ord-123")
    mock_broker.get_order_by_id.return_value = filled

    trade = {
        "ticker": "AAPL", "shares": 10, "pool": 2, "action": "BUY",
        "entry_price": 150.0, "target_price": 156.0, "stop_loss": 147.0,
        "position_size": 1500, "confidence": "MEDIUM",
    }

    with patch("agents.alpaca_broker.db") as mock_db:
        mock_db.insert.return_value = None
        broker_mod.place_orders([trade])

    # First submit_order is the bracket order
    bracket_req = mock_broker.submit_order.call_args_list[0][0][0]
    assert isinstance(bracket_req, LimitOrderRequest)
    # Bid price of ask=150.10, bid=150.00 → 150.00 (tight spread → bid, passive-first)
    assert bracket_req.limit_price == pytest.approx(150.00, abs=0.01)
    stop_req = bracket_req.stop_loss
    assert isinstance(stop_req, StopLossRequest)
    assert stop_req.stop_price is not None
    assert not hasattr(stop_req, "trail_percent") or stop_req.trail_percent is None
    # Second submit_order call is the trailing stop
    assert mock_broker.submit_order.call_count == 2


@patch("agents.alpaca_broker.get_live_quotes")
@patch("agents.alpaca_broker._get")
def test_place_orders_wide_spread_uses_bid_anchored_stop(mock_get, mock_quotes):
    """Wide spread (0.2–5%): limit = plan price, stop/target anchored to bid so stop < fill always."""
    import agents.alpaca_broker as broker_mod
    from alpaca.trading.requests import LimitOrderRequest, StopLossRequest

    # ask=150.50, bid=150.00 → 0.33% spread (wide, not extreme)
    mock_quotes.return_value = {"AAPL": {"ask": 150.50, "bid": 150.00}}

    filled = MagicMock()
    filled.id = "ord-456"
    filled.status = "filled"
    filled.filled_avg_price = 150.00

    mock_broker = mock_get.return_value
    mock_broker.submit_order.return_value = MagicMock(id="ord-456")
    mock_broker.get_order_by_id.return_value = filled

    # plan: entry=150, stop=147 (2% below entry), target=156 (4% above)
    trade = {
        "ticker": "AAPL", "shares": 10, "pool": 2, "action": "BUY",
        "entry_price": 150.0, "target_price": 156.0, "stop_loss": 147.0,
        "position_size": 1500, "confidence": "MEDIUM",
    }

    with patch("agents.alpaca_broker.db") as mock_db:
        mock_db.insert.return_value = None
        result = broker_mod.place_orders([trade])

    mock_broker.submit_order.assert_called()
    bracket_req = mock_broker.submit_order.call_args_list[0][0][0]
    assert isinstance(bracket_req, LimitOrderRequest)
    # Limit must be plan entry price
    assert bracket_req.limit_price == pytest.approx(150.0, abs=0.01)
    # Stop must be anchored to bid (150.00), not plan entry (150.0)
    # plan_stop_pct = (150 - 147) / 150 = 0.02  →  stop = 150.00 * (1 - 0.02) = 147.00
    stop_req = bracket_req.stop_loss
    assert isinstance(stop_req, StopLossRequest)
    assert stop_req.stop_price == pytest.approx(147.0, abs=0.02)


@patch("agents.alpaca_broker.get_live_quotes")
@patch("agents.alpaca_broker._get")
def test_place_orders_extreme_spread_skips(mock_get, mock_quotes):
    """Extreme spread (>5%) must skip the order — quote data is unreliable."""
    import agents.alpaca_broker as broker_mod

    # ask=150.0, bid=142.0 → 5.3% spread (extreme)
    mock_quotes.return_value = {"AAPL": {"ask": 150.0, "bid": 142.0}}

    mock_broker = mock_get.return_value
    trade = {
        "ticker": "AAPL", "shares": 10, "pool": 2, "action": "BUY",
        "entry_price": 150.0, "target_price": 156.0, "stop_loss": 147.0,
        "position_size": 1500, "confidence": "MEDIUM",
    }

    with patch("agents.alpaca_broker.db"):
        result = broker_mod.place_orders([trade])

    assert result == []
    mock_broker.submit_order.assert_not_called()


# ── hybrid_limit_price ────────────────────────────────────────────────────────

from agents.alpaca_broker import hybrid_limit_price

def test_hybrid_tight_spread_returns_bid():
    """Spread < 0.10% → bid (passive fill, stock dips to us)."""
    ask, bid = 100.10, 100.00
    result = hybrid_limit_price(ask, bid)
    assert result == round(bid, 2)

def test_hybrid_moderate_spread_returns_mid():
    """Spread 0.10–0.20% → mid (was ask — shifted one tier lower)."""
    ask, bid = 100.15, 100.00
    result = hybrid_limit_price(ask, bid)
    assert result == round((ask + bid) / 2, 2)

def test_hybrid_wide_spread_returns_none():
    """Spread > 0.20% → None (skip)."""
    ask, bid = 100.30, 100.00
    result = hybrid_limit_price(ask, bid)
    assert result is None

def test_hybrid_zero_ask_returns_none():
    assert hybrid_limit_price(0.0, 99.0) is None

def test_hybrid_ask_less_than_bid_returns_ask():
    result = hybrid_limit_price(99.0, 100.0)
    assert result == round(99.0, 2)


# ── Trail backfill in update_positions_intraday ───────────────────────────────

def _open_pos_no_trail(ticker="ORCL", fill=203.24, entry=203.24, shares=34):
    return {
        "id": "pos-orcl",
        "ticker": ticker,
        "shares": shares,
        "entry_price": entry,
        "fill_price": fill,
        "target_price": 226.0,
        "stop_loss": 211.60,
        "high_watermark": 223.94,
        "low_watermark": fill,
        "current_price": 222.0,
        "unrealized_pnl": shares * (222.0 - entry),
        "trail_order_id": None,
        "status": "OPEN",
    }


@patch("agents.alpaca_broker.USE_NATIVE_TRAILING_STOP", True)
@patch("agents.alpaca_broker.TRAIL_PCT", 0.01)
@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker.get_current_price", return_value=222.0)
@patch("agents.alpaca_broker.db")
@patch("agents.alpaca_broker._get")
def test_trail_backfill_submitted_for_filled_position(
    mock_get, mock_db, mock_price, mock_signals
):
    """Confirmed-filled position with no trail_order_id gets a trail submitted on next cycle."""
    pos = _open_pos_no_trail()

    # Alpaca has the position open
    alpaca_pos = MagicMock(); alpaca_pos.symbol = "ORCL"
    mock_get.return_value.get_all_positions.return_value = [alpaca_pos]
    mock_get.return_value.get_orders.return_value = []

    trail_order = MagicMock(); trail_order.id = "trail-abc-123"
    mock_get.return_value.submit_order.return_value = trail_order

    mock_db.select.return_value = [pos]
    mock_db.update.return_value = None

    update_positions_intraday()

    # Trail should have been submitted
    submitted = mock_get.return_value.submit_order.call_args_list
    trail_calls = [c for c in submitted if "trailing" in str(c).lower() or
                   hasattr(c[0][0], "trail_percent")]
    assert len(trail_calls) >= 1, "Expected trailing stop to be submitted for unfilled trail position"

    # DB should be updated with trail_order_id
    update_calls = [c for c in mock_db.update.call_args_list
                    if "trail_order_id" in str(c)]
    assert len(update_calls) >= 1, "Expected DB update with trail_order_id"


@patch("agents.alpaca_broker.USE_NATIVE_TRAILING_STOP", True)
@patch("agents.alpaca_broker.TRAIL_PCT", 0.01)
@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker.get_current_price", return_value=222.0)
@patch("agents.alpaca_broker.db")
@patch("agents.alpaca_broker._get")
def test_trail_backfill_skipped_for_unfilled_position(
    mock_get, mock_db, mock_price, mock_signals
):
    """Position with fill_price=None (entry not confirmed) does NOT get a trail submitted."""
    pos = _open_pos_no_trail(fill=None)

    alpaca_pos = MagicMock(); alpaca_pos.symbol = "ORCL"
    mock_get.return_value.get_all_positions.return_value = [alpaca_pos]
    mock_get.return_value.get_orders.return_value = []
    mock_db.select.return_value = [pos]

    update_positions_intraday()

    # Trail must NOT be submitted for unfilled positions
    submitted = mock_get.return_value.submit_order.call_args_list
    trail_calls = [c for c in submitted if "trailing" in str(c).lower() or
                   hasattr(c[0][0], "trail_percent")]
    assert len(trail_calls) == 0, "Trail must not be submitted for unconfirmed-fill position"


@patch("agents.alpaca_broker.USE_NATIVE_TRAILING_STOP", True)
@patch("agents.alpaca_broker.TRAIL_PCT", 0.01)
@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker.get_current_price", return_value=222.0)
@patch("agents.alpaca_broker.db")
@patch("agents.alpaca_broker._get")
def test_fill_price_backfilled_when_order_confirmed(mock_get, mock_db, mock_price, mock_signals):
    """Position with fill_price=None gets fill_price backfilled when Alpaca shows order filled."""
    pos = {**_open_pos_no_trail(fill=None), "alpaca_order_id": "order-abc-123"}

    alpaca_pos = MagicMock(); alpaca_pos.symbol = "ORCL"
    mock_get.return_value.get_all_positions.return_value = [alpaca_pos]
    mock_get.return_value.get_orders.return_value = []

    # Alpaca order shows filled
    filled_order = MagicMock()
    filled_order.status = "filled"
    filled_order.filled_avg_price = 203.10
    mock_get.return_value.get_order_by_id.return_value = filled_order

    trail_order = MagicMock(); trail_order.id = "trail-xyz"
    mock_get.return_value.submit_order.return_value = trail_order
    mock_db.select.return_value = [pos]
    mock_db.update.return_value = None

    update_positions_intraday()

    # fill_price should have been written to DB
    fill_updates = [c for c in mock_db.update.call_args_list if "fill_price" in str(c)]
    assert len(fill_updates) >= 1, "Expected fill_price to be written to DB"
    # Trail should then have been submitted
    trail_calls = [c for c in mock_get.return_value.submit_order.call_args_list
                   if hasattr(c[0][0], "trail_percent")]
    assert len(trail_calls) >= 1, "Expected trailing stop submitted after fill_price backfill"


@patch("agents.alpaca_broker.USE_NATIVE_TRAILING_STOP", True)
@patch("agents.alpaca_broker.TRAIL_PCT", 0.01)
@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker.get_current_price", return_value=222.0)
@patch("agents.alpaca_broker.db")
@patch("agents.alpaca_broker._get")
def test_trail_backfill_skipped_when_not_in_alpaca(
    mock_get, mock_db, mock_price, mock_signals
):
    """Filled position not in Alpaca open positions does not get a trail (may be exiting)."""
    pos = _open_pos_no_trail()

    mock_get.return_value.get_all_positions.return_value = []  # empty — not in Alpaca
    mock_get.return_value.get_orders.return_value = []
    mock_db.select.return_value = [pos]

    update_positions_intraday()

    submitted = mock_get.return_value.submit_order.call_args_list
    trail_calls = [c for c in submitted if "trailing" in str(c).lower() or
                   hasattr(c[0][0], "trail_percent")]
    assert len(trail_calls) == 0, "Trail must not be submitted if position not confirmed in Alpaca"


# ── Qty sync (partial-fill correction) ───────────────────────────────────────

@patch("agents.alpaca_broker.USE_NATIVE_TRAILING_STOP", False)
@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker.get_current_price", return_value=222.0)
@patch("agents.alpaca_broker.db")
@patch("agents.alpaca_broker._get")
def test_qty_sync_corrects_partial_fill(mock_get, mock_db, mock_price, mock_signals):
    """When Alpaca holds fewer shares than DB (partial fill + cancel), shares is corrected."""
    pos = _open_pos_no_trail(shares=12)

    alpaca_pos = MagicMock()
    alpaca_pos.symbol = "ORCL"
    alpaca_pos.qty = "8"  # Alpaca string — partial fill
    mock_get.return_value.get_all_positions.return_value = [alpaca_pos]
    mock_get.return_value.get_orders.return_value = []
    mock_db.select.return_value = [pos]
    mock_db.update.return_value = None

    update_positions_intraday()

    qty_updates = [c for c in mock_db.update.call_args_list if "shares" in str(c)]
    assert len(qty_updates) >= 1, "Expected DB update to correct shares"
    updated_qty = qty_updates[0][0][2]["shares"]
    assert updated_qty == 8, f"Expected shares=8, got {updated_qty}"


@patch("agents.alpaca_broker.USE_NATIVE_TRAILING_STOP", False)
@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker.get_current_price", return_value=222.0)
@patch("agents.alpaca_broker.db")
@patch("agents.alpaca_broker._get")
def test_qty_sync_skipped_when_shares_match(mock_get, mock_db, mock_price, mock_signals):
    """No DB update when Alpaca qty matches DB shares."""
    pos = _open_pos_no_trail(shares=12)

    alpaca_pos = MagicMock()
    alpaca_pos.symbol = "ORCL"
    alpaca_pos.qty = "12"
    mock_get.return_value.get_all_positions.return_value = [alpaca_pos]
    mock_get.return_value.get_orders.return_value = []
    mock_db.select.return_value = [pos]
    mock_db.update.return_value = None

    update_positions_intraday()

    qty_updates = [c for c in mock_db.update.call_args_list if "shares" in str(c)]
    assert len(qty_updates) == 0, "No shares update expected when qty already matches"


@patch("agents.alpaca_broker.USE_NATIVE_TRAILING_STOP", True)
@patch("agents.alpaca_broker.TRAIL_PCT", 0.01)
@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker.get_current_price", return_value=222.0)
@patch("agents.alpaca_broker.db")
@patch("agents.alpaca_broker._get")
def test_trail_backfill_uses_corrected_shares(mock_get, mock_db, mock_price, mock_signals):
    """After qty_sync corrects shares to 8, trail backfill submits for 8 shares not 12."""
    pos = _open_pos_no_trail(shares=12)

    alpaca_pos = MagicMock()
    alpaca_pos.symbol = "ORCL"
    alpaca_pos.qty = "8"
    mock_get.return_value.get_all_positions.return_value = [alpaca_pos]
    mock_get.return_value.get_orders.return_value = []

    trail_order = MagicMock(); trail_order.id = "trail-partial-001"
    mock_get.return_value.submit_order.return_value = trail_order
    mock_db.select.return_value = [pos]
    mock_db.update.return_value = None

    import agents.alpaca_broker as broker_mod
    with patch.object(broker_mod, "_cancel_bracket_stop_leg"):
        update_positions_intraday()

    submitted = mock_get.return_value.submit_order.call_args_list
    trail_calls = [c for c in submitted if hasattr(c[0][0], "trail_percent")]
    assert len(trail_calls) >= 1, "Trail must be submitted"
    req = trail_calls[0][0][0]
    assert int(req.qty) == 8, f"Trail must use corrected qty=8, got {req.qty}"


# ── Manual trailing stop fallback ────────────────────────────────────────────

def _pos_no_trail(entry=100.0, fill=100.0, shares=10, high_wm=115.0, stop=95.0, price=111.5):
    """Position with no native trail active — high watermark set above entry."""
    return {
        "id":             "pos-manual",
        "ticker":         "AAPL",
        "shares":         shares,
        "entry_price":    entry,
        "fill_price":     fill,
        "target_price":   120.0,
        "stop_loss":      stop,
        "high_watermark": high_wm,
        "low_watermark":  fill,
        "current_price":  price,
        "unrealized_pnl": shares * (price - entry),
        "trail_order_id": None,  # no native trail
        "status":         "OPEN",
    }


@patch("agents.alpaca_broker.TRAIL_PCT", 0.01)
@patch("agents.alpaca_broker._close_position")
@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker._get")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_manual_trail_fires_when_no_native_trail(
    mock_price, mock_update, mock_select, mock_get, mock_signals, mock_close
):
    """When trail_order_id is None and price drops > TRAIL_PCT from peak, MANUAL_TRAIL fires."""
    mock_get.return_value = _alpaca_mock_with_open("AAPL")
    # Peak = 115.0, TRAIL_PCT = 1% → eff_stop = 115.0 * 0.99 = 113.85
    # price = 113.0 < 113.85 and price > stop (95.0) → should fire
    pos = _pos_no_trail(high_wm=115.0, stop=95.0, price=113.0)
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 113.0

    update_positions_intraday()

    mock_close.assert_called_once()
    assert mock_close.call_args[0][2] == "MANUAL_TRAIL"


@patch("agents.alpaca_broker.TRAIL_PCT", 0.01)
@patch("agents.alpaca_broker._close_position")
@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker._get")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_manual_trail_does_not_fire_when_native_trail_active(
    mock_price, mock_update, mock_select, mock_get, mock_signals, mock_close
):
    """When trail_order_id is set, manual trail must not fire (Alpaca handles it)."""
    mock_get.return_value = _alpaca_mock_with_open("AAPL")
    pos = _pos_no_trail(high_wm=115.0, stop=95.0, price=113.0)
    pos["trail_order_id"] = "trail-active-abc"  # native trail is live
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 113.0

    update_positions_intraday()

    mock_close.assert_not_called()


@patch("agents.alpaca_broker.TRAIL_PCT", 0.01)
@patch("agents.alpaca_broker._close_position")
@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker._get")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_manual_trail_does_not_fire_above_eff_stop(
    mock_price, mock_update, mock_select, mock_get, mock_signals, mock_close
):
    """Price above eff_stop — manual trail must not fire."""
    mock_get.return_value = _alpaca_mock_with_open("AAPL")
    # Peak = 115.0, eff_stop = 113.85; price = 114.5 > 113.85 → no fire
    pos = _pos_no_trail(high_wm=115.0, stop=95.0, price=114.5)
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 114.5

    update_positions_intraday()

    mock_close.assert_not_called()


# ── _cancel_bracket_stop_leg ─────────────────────────────────────────────────

def _make_stop_leg(order_type="stop", status="new", leg_id="leg-b-001"):
    leg = MagicMock()
    leg.order_type = order_type
    leg.status     = status
    leg.id         = leg_id
    return leg


@patch("agents.alpaca_broker._get")
def test_cancel_bracket_stop_leg_cancels_open_stop(mock_get):
    """Open stop leg is cancelled so trailing stop can be submitted."""
    stop_leg = _make_stop_leg("stop", "new")
    order = MagicMock(); order.symbol = "AAPL"; order.legs = [stop_leg]
    mock_get.return_value.get_order_by_id.return_value = order

    from agents.alpaca_broker import _cancel_bracket_stop_leg
    _cancel_bracket_stop_leg("ord-b-001")

    mock_get.return_value.cancel_order_by_id.assert_called_once_with("leg-b-001")


@patch("agents.alpaca_broker._get")
def test_cancel_bracket_stop_leg_skips_already_cancelled(mock_get):
    stop_leg = _make_stop_leg("stop", "canceled")
    order = MagicMock(); order.symbol = "AAPL"; order.legs = [stop_leg]
    mock_get.return_value.get_order_by_id.return_value = order

    from agents.alpaca_broker import _cancel_bracket_stop_leg
    _cancel_bracket_stop_leg("ord-b-001")

    mock_get.return_value.cancel_order_by_id.assert_not_called()


@patch("agents.alpaca_broker.USE_NATIVE_TRAILING_STOP", True)
@patch("agents.alpaca_broker.TRAIL_PCT", 0.01)
@patch("agents.alpaca_broker.get_live_quotes")
@patch("agents.alpaca_broker._get")
def test_place_orders_cancels_stop_leg_before_trail(mock_get, mock_quotes):
    """_cancel_bracket_stop_leg is called before submit_trailing_stop at placement."""
    from alpaca.trading.requests import LimitOrderRequest

    mock_quotes.return_value = {"AAPL": {"ask": 150.10, "bid": 150.00}}

    filled = MagicMock()
    filled.id = "ord-place-001"
    filled.status = "filled"
    filled.filled_avg_price = 150.00

    mock_get.return_value.submit_order.return_value = MagicMock(id="ord-place-001")
    mock_get.return_value.get_order_by_id.return_value = filled

    cancel_calls = []
    def fake_cancel_stop_leg(order_id):
        cancel_calls.append(order_id)
    trail_calls = []
    def fake_submit_trail(*args, **kwargs):
        trail_calls.append(args)
        return "trail-place-001"

    import agents.alpaca_broker as broker_mod
    with patch.object(broker_mod, "_cancel_bracket_stop_leg", side_effect=fake_cancel_stop_leg), \
         patch.object(broker_mod, "submit_trailing_stop", side_effect=fake_submit_trail), \
         patch("agents.alpaca_broker.db") as mock_db:
        mock_db.insert.return_value = None
        broker_mod.place_orders([{
            "ticker": "AAPL", "shares": 10, "pool": 2, "action": "BUY",
            "entry_price": 150.0, "target_price": 156.0, "stop_loss": 147.0,
            "position_size": 1500, "confidence": "HIGH",
        }])

    assert len(cancel_calls) == 1, "_cancel_bracket_stop_leg must be called once"
    assert len(trail_calls) == 1, "submit_trailing_stop must be called once"


@patch("agents.alpaca_broker.USE_NATIVE_TRAILING_STOP", True)
@patch("agents.alpaca_broker.TRAIL_PCT", 0.01)
@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker.get_current_price", return_value=222.0)
@patch("agents.alpaca_broker.db")
@patch("agents.alpaca_broker._get")
def test_trail_backfill_calls_cancel_stop_leg(mock_get, mock_db, mock_price, mock_signals):
    """Backfill retry in update_positions_intraday cancels stop leg before submitting trail."""
    pos = _open_pos_no_trail()
    pos["alpaca_order_id"] = "ord-orcl-bracket-001"
    alpaca_pos = MagicMock(); alpaca_pos.symbol = "ORCL"
    mock_get.return_value.get_all_positions.return_value = [alpaca_pos]
    mock_get.return_value.get_orders.return_value = []
    trail_order = MagicMock(); trail_order.id = "trail-backfill-001"
    mock_get.return_value.submit_order.return_value = trail_order
    mock_db.select.return_value = [pos]
    mock_db.update.return_value = None

    cancel_calls = []
    import agents.alpaca_broker as broker_mod
    with patch.object(broker_mod, "_cancel_bracket_stop_leg", side_effect=lambda oid: cancel_calls.append(oid)):
        update_positions_intraday()

    assert len(cancel_calls) >= 1, "_cancel_bracket_stop_leg must be called during backfill"


@patch("agents.alpaca_broker.USE_NATIVE_TRAILING_STOP", True)
@patch("agents.alpaca_broker.TRAIL_PCT", 0.01)
@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker.get_current_price", return_value=222.0)
@patch("agents.alpaca_broker.db")
@patch("agents.alpaca_broker._get")
def test_trail_backfill_logs_warning_when_trail_fails(
    mock_get, mock_db, mock_price, mock_signals, capsys
):
    """When trail submission fails (e.g. bracket legs still held), backfill logs a warning
    and leaves trail_order_id None for retry on the next cycle."""
    pos = _open_pos_no_trail()
    alpaca_pos = MagicMock(); alpaca_pos.symbol = "ORCL"
    mock_get.return_value.get_all_positions.return_value = [alpaca_pos]
    mock_get.return_value.get_orders.return_value = []
    # submit_order raises "insufficient qty" — bracket legs still held
    mock_get.return_value.submit_order.side_effect = Exception("insufficient qty available")
    mock_db.select.return_value = [pos]
    mock_db.update.return_value = None

    import agents.alpaca_broker as broker_mod
    with patch.object(broker_mod, "_cancel_bracket_stop_leg"):
        update_positions_intraday()

    out = capsys.readouterr().out
    assert "still pending" in out or "trail" in out.lower(), \
        "Expected warning log when trail backfill fails"
    # DB should NOT be updated with a trail_order_id
    trail_updates = [c for c in mock_db.update.call_args_list
                     if "trail_order_id" in str(c)]
    assert len(trail_updates) == 0, "trail_order_id must not be written when submission failed"


# ── Double-sell guard ─────────────────────────────────────────────────────────

@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker._get")
def test_close_position_no_sell_when_alpaca_already_flat(mock_get, mock_update):
    """If Alpaca has no open position, _close_position marks DB closed without submitting a sell.
    Prevents double-sell creating a short when a prior cycle already closed the position."""
    mock_get.return_value.get_open_position.side_effect = Exception("position does not exist")

    pos = _pos(fill=100.0, shares=10)
    _close_position(pos, price=97.0, reason="STOP")

    mock_get.return_value.submit_order.assert_not_called()
    update_kwargs = mock_update.call_args[0][2]
    assert update_kwargs["status"] == "CLOSED"
    assert update_kwargs["close_reason"] == "STOP"


@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker._get")
def test_close_position_confirms_fill_with_enum_status(mock_get, mock_update):
    """close_confirmed must be set when Alpaca returns status as an enum object (not plain string)."""
    alpaca_pos = MagicMock()
    alpaca_pos.symbol = "AAPL"
    mock_get.return_value.get_open_position.return_value = alpaca_pos

    filled_mock = MagicMock()
    filled_mock.status = MagicMock()
    filled_mock.status.value = "filled"
    filled_mock.filled_avg_price = 97.0
    mock_get.return_value.submit_order.return_value = MagicMock()
    mock_get.return_value.get_order_by_id.return_value = filled_mock

    pos = _pos(fill=100.0, shares=10)
    _close_position(pos, price=97.0, reason="STOP")

    update_kwargs = mock_update.call_args[0][2]
    assert update_kwargs["status"] == "CLOSED"


# ── Intraday loop: unrealized P&L uses fill_price, not entry_price ────────────

@patch("agents.alpaca_broker.get_intraday_signals", return_value={})
@patch("agents.alpaca_broker._get")
@patch("agents.alpaca_broker.db.select")
@patch("agents.alpaca_broker.db.update")
@patch("agents.alpaca_broker.get_current_price")
def test_unrealized_pnl_uses_fill_price_not_entry(mock_price, mock_update, mock_select, mock_get, mock_signals):
    """
    Regression: ORCL 2026-06-04 — filled at $233.74 but entry_price=$240.
    Intraday loop was computing (price - $240) giving a false -$41 loss.
    After fix: unrealized uses fill_price so P&L reflects actual cost.
    """
    mock_get.return_value = _alpaca_mock_with_open("ORCL", qty=12)
    pos = _pos(entry=240.0, fill=233.74, shares=12, high_wm=233.74, low_wm=233.74, cur=233.74)
    pos["ticker"] = "ORCL"
    pos["target_price"] = 252.28
    pos["stop_loss"] = 232.02
    mock_select.side_effect = lambda table, **kw: [pos] if kw.get("filters", {}).get("status") == "OPEN" else []
    mock_price.return_value = 236.55

    update_positions_intraday()

    pnl_calls = [c for c in mock_update.call_args_list if "unrealized_pnl" in c[0][2]]
    assert pnl_calls, "Expected db.update with unrealized_pnl"
    computed_pnl = pnl_calls[0][0][2]["unrealized_pnl"]
    expected_pnl = round(12 * (236.55 - 233.74), 2)   # +$33.72 using fill, not -$41.40 from entry
    assert computed_pnl == expected_pnl, f"Got {computed_pnl}, expected {expected_pnl}"
