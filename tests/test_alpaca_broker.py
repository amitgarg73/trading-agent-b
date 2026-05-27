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

@patch("agents.alpaca_broker._get")
def test_place_orders_uses_stop_price_not_trail_percent(mock_get):
    """place_orders must pass stop_price to StopLossRequest — trail_percent is invalid and causes API rejection."""
    from unittest.mock import MagicMock
    import agents.alpaca_broker as broker_mod

    filled = MagicMock()
    filled.id = "ord-123"
    filled.status = "filled"
    filled.filled_avg_price = 150.0

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

    from alpaca.trading.requests import StopLossRequest
    # First submit_order call is the bracket; second is the trailing stop.
    bracket_req = mock_broker.submit_order.call_args_list[0][0][0]
    stop_req = bracket_req.stop_loss
    assert isinstance(stop_req, StopLossRequest)
    assert stop_req.stop_price == pytest.approx(147.0)
    assert not hasattr(stop_req, "trail_percent") or stop_req.trail_percent is None
    # Second call is the trailing stop (not a bracket)
    assert mock_broker.submit_order.call_count == 2
