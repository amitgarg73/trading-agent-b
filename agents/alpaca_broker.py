"""
Alpaca Broker — places and manages orders for Strategy B.
Uses same Alpaca paper account as Strategy A.
All orders include strategy_b tag via client_order_id prefix.
"""
from __future__ import annotations
import os
import time
from datetime import date, datetime
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest, LimitOrderRequest, GetOrdersRequest,
    TakeProfitRequest, StopLossRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus, QueryOrderStatus, OrderClass
from core import db
from config.settings import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, STRATEGY_TAG,
    TRAIL_PCT, USE_NATIVE_TRAILING_STOP,
    PARTIAL_PROFIT_ENABLED, PARTIAL_PROFIT_PCT,
    DAILY_LOCK_IN_TARGET, DAILY_BONUS_TARGET, LOCK_IN_TRAIL_PCT,
    R_LADDER_ENABLED, VWAP_EXIT_ENABLED,
)

_client: TradingClient | None = None
_data_client = None


def _get() -> TradingClient:
    global _client
    if _client is None:
        _client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
    return _client


def _dclient():
    global _data_client
    if _data_client is None:
        from alpaca.data import StockHistoricalDataClient
        _data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    return _data_client


def _order_id(ticker: str) -> str:
    """Unique client order ID with strategy tag for easy filtering."""
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"strat{STRATEGY_TAG}_{ticker}_{ts}"


def get_live_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch real-time ask prices via Alpaca quotes. Returns {ticker: price}."""
    if not tickers:
        return {}
    try:
        from alpaca.data.requests import StockLatestQuoteRequest
        quotes = _dclient().get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=tickers)
        )
        prices = {}
        for ticker, quote in quotes.items():
            ask = getattr(quote, "ask_price", None)
            bid = getattr(quote, "bid_price", None)
            if ask and float(ask) > 0:
                prices[ticker] = round(float(ask), 4)
            elif bid and float(bid) > 0:
                prices[ticker] = round(float(bid), 4)
        return prices
    except Exception as e:
        print(f"        ⚠️  Live price fetch failed: {e}")
        return {}


def get_intraday_signals(tickers: list[str]) -> dict[str, dict]:
    """
    Fetch intraday signals via Alpaca snapshot API.
    Returns {ticker: {above_vwap, vwap, today_pct_change, rs_vs_spy}}.
    SPY is fetched as the RS baseline.
    """
    if not tickers:
        return {}
    all_tickers = list(set(tickers + ["SPY"]))
    try:
        from alpaca.data.requests import StockSnapshotRequest
        snapshots = _dclient().get_stock_snapshot(
            StockSnapshotRequest(symbol_or_symbols=all_tickers)
        )

        spy_snap = snapshots.get("SPY")
        spy_pct  = None
        if spy_snap and spy_snap.daily_bar:
            spy_open  = getattr(spy_snap.daily_bar, "open", None)
            spy_price = (getattr(spy_snap.latest_trade, "price", None)
                         or getattr(spy_snap.daily_bar, "close", None))
            if spy_open and spy_price and float(spy_open) > 0:
                spy_pct = (float(spy_price) - float(spy_open)) / float(spy_open)

        signals = {}
        for ticker in tickers:
            snap = snapshots.get(ticker)
            if not snap or not snap.daily_bar:
                continue
            vwap    = getattr(snap.daily_bar, "vwap",  None)
            open_px = getattr(snap.daily_bar, "open",  None)
            price   = (getattr(snap.latest_trade, "price", None)
                       or getattr(snap.daily_bar, "close", None))
            if not (vwap and open_px and price):
                continue
            vwap, open_px, price = float(vwap), float(open_px), float(price)
            today_pct = (price - open_px) / open_px if open_px > 0 else 0.0
            rs_vs_spy = round(today_pct / spy_pct, 2) if spy_pct else None
            signals[ticker] = {
                "above_vwap":       price > vwap,
                "vwap":             round(vwap, 2),
                "today_pct_change": round(today_pct * 100, 2),
                "rs_vs_spy":        rs_vs_spy,
            }
        return signals
    except Exception as e:
        print(f"        ⚠️  Intraday signals fetch failed: {e}")
        return {}


def get_current_price(ticker: str) -> float | None:
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest
        data = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
        req  = StockLatestTradeRequest(symbol_or_symbols=ticker)
        trade = data.get_stock_latest_trade(req)
        return float(trade[ticker].price)
    except Exception:
        return None


def place_orders(trades: list[dict], run_id: str | None = None) -> list[dict]:
    """Place market buy orders for approved trades. Returns list with order_ids."""
    broker  = _get()
    placed  = []

    for trade in trades:
        ticker = trade["ticker"]
        shares = int(trade["shares"])
        pool   = trade.get("pool", 2)

        try:
            target_price = round(float(trade["target_price"]), 2)
            req = MarketOrderRequest(
                symbol=ticker,
                qty=shares,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=target_price),
                stop_loss=StopLossRequest(trail_percent=round(TRAIL_PCT * 100, 4)),
                client_order_id=_order_id(ticker),
            )
            order = broker.submit_order(req)
            print(f"[alpaca] BUY {shares} {ticker} @ market bracket → target={target_price} trail={TRAIL_PCT*100:.1f}% (pool {pool}) — order {order.id}")

            # Verify order filled (not cancelled/rejected) before writing to DB
            fill_price     = None
            order_accepted = False
            for _ in range(15):
                time.sleep(1)
                try:
                    filled = broker.get_order_by_id(str(order.id))
                    status = str(filled.status).lower()
                    if status in ("filled", "partially_filled"):
                        fill_price     = float(filled.filled_avg_price) if filled.filled_avg_price else None
                        order_accepted = True
                        break
                    elif status in ("cancelled", "rejected", "expired"):
                        print(f"[alpaca] {ticker} order {status} — skipping DB write")
                        break
                except Exception:
                    pass

            if not order_accepted:
                print(f"[alpaca] {ticker} — could not confirm fill after 15s, skipping DB write")
                continue  # don't write a phantom position

            planned_entry = float(trade["entry_price"])
            if fill_price:
                slippage_bps = round(abs(fill_price - planned_entry) / planned_entry * 10_000, 1)
                print(f"[alpaca] {ticker} fill=${fill_price:.2f} planned=${planned_entry:.2f} slip={slippage_bps}bps")
            else:
                fill_price = planned_entry

            # Write to b_positions
            db.insert("b_positions", {
                "ticker":          ticker,
                "pool":            pool,
                "action":          "BUY",
                "entry_price":     planned_entry,
                "fill_price":      fill_price,
                "target_price":    trade["target_price"],
                "stop_loss":       trade["stop_loss"],
                "shares":          shares,
                "position_size":   trade["position_size"],
                "status":          "OPEN",
                "alpaca_order_id": str(order.id),
                "high_watermark":  fill_price,
                "low_watermark":   fill_price,
                "run_id":          run_id,
            })

            placed.append({**trade, "alpaca_order_id": str(order.id)})
            time.sleep(0.3)

        except Exception as e:
            print(f"[alpaca] Failed to place {ticker}: {e}")

    return placed


def open_positions() -> list[dict]:
    return db.select("b_positions", filters={"status": "OPEN"})


def get_open_tickers() -> set:
    """Return set of ticker symbols currently held in Alpaca."""
    positions = _get().get_all_positions()
    return {p.symbol for p in positions}


def get_order_fill(order_id: str):
    """Return (close_price, exit_mechanism) for a completed bracket order."""
    try:
        order = _get().get_order_by_id(order_id)
        legs = order.legs or []
        for leg in legs:
            status_str = str(leg.status).lower()
            type_str   = str(leg.order_type).lower()
            if "filled" in status_str and leg.filled_avg_price:
                if "trailing" in type_str:
                    return float(leg.filled_avg_price), "NATIVE_TRAIL"
                elif "stop" in type_str:
                    return float(leg.filled_avg_price), "STOP"
                else:
                    return float(leg.filled_avg_price), "TARGET"
    except Exception as e:
        print(f"  ⚠️  get_order_fill({order_id}): {e}")
    return None, None


def _reconcile_with_alpaca() -> None:
    """
    Close any b_positions OPEN rows that no longer exist in Alpaca.
    Two cases:
      - Entry filled + bracket exited (stop/target fired natively) → CLOSED with real P&L
      - Entry never filled and no pending buy → UNFILLED at $0
    """
    alpaca_tickers = get_open_tickers()
    positions = open_positions()
    if not positions:
        return

    # get_orders() returns parent orders only — bracket child legs (stop/target) are not
    # included, so filled_sells would always be empty for bracket exits. Instead track
    # filled_buys: if entry filled, the bracket exited; update_positions_intraday()'s
    # manual trail/stop/target logic resolves P&L. Only mark UNFILLED when no buy
    # order ever filled or is pending — entry truly never executed.
    try:
        all_orders = _get().get_orders(
            GetOrdersRequest(status=QueryOrderStatus.ALL, limit=100)
        )
        today = datetime.utcnow().date().isoformat()
        filled_buys = {
            str(o.symbol)
            for o in all_orders
            if str(o.side) == "buy"
            and str(o.status) == "filled"
            and str(o.filled_at or o.submitted_at or "").startswith(today[:10])
        }
        pending_buys = {
            str(o.symbol)
            for o in all_orders
            if str(o.side) == "buy"
            and str(o.status) in ("pending_new", "accepted", "new", "held", "partially_filled")
            and str(o.submitted_at or "").startswith(today[:10])
        }
    except Exception as e:
        print(f"  ⚠️  Reconciliation: order fetch failed — {e}")
        filled_buys  = set()
        pending_buys = set()

    for pos in positions:
        if pos["ticker"] in alpaca_tickers:
            continue
        if pos["ticker"] in pending_buys:
            print(f"  ⏳ Reconciliation: {pos['ticker']} buy order pending — waiting for fill")
            continue
        if pos["ticker"] in filled_buys:
            # Entry filled — bracket leg may have fired. Resolve it here if position is gone.
            order_id = pos.get("alpaca_order_id")
            if order_id:
                close_price, mechanism = get_order_fill(order_id)
                if close_price:
                    entry  = float(pos.get("fill_price") or pos["entry_price"])
                    shares = int(pos["shares"])
                    pnl    = round(shares * (close_price - entry), 2)
                    hwm    = float(pos.get("high_watermark") or entry)
                    lwm    = float(pos.get("low_watermark")  or entry)
                    db.update("b_positions", {"id": pos["id"]}, {
                        "status":         "CLOSED",
                        "close_reason":   mechanism or "BRACKET",
                        "exit_mechanism": mechanism or "BRACKET",
                        "close_price":    close_price,
                        "realized_pnl":   pnl,
                        "closed_at":      datetime.utcnow().isoformat(),
                        "mae":            round(max(0.0, (entry - lwm) * shares), 2),
                        "mfe":            round(max(0.0, (hwm  - entry) * shares), 2),
                    })
                    print(f"  ✅ Bracket exit: {pos['ticker']} → {mechanism} @ ${close_price:.2f} P&L=${pnl:+.2f}")
                else:
                    print(f"  ⚠️  NATIVE_TRAIL: {pos['ticker']} gone from Alpaca but get_order_fill returned no price — position stays OPEN, will retry next cycle")
            continue
        # No filled buy and no pending buy — entry truly never executed
        print(f"  ⚠️  Reconciliation: {pos['ticker']} OPEN in DB but not in Alpaca — marking UNFILLED")
        db.update("b_positions", {"id": pos["id"]}, {
            "status":         "CLOSED",
            "close_reason":   "UNFILLED",
            "exit_mechanism": "UNFILLED",
            "closed_at":      datetime.utcnow().isoformat(),
            "realized_pnl":   0,
            "close_price":    pos.get("entry_price"),
        })


def update_positions_intraday() -> dict:
    """
    Reconcile with Alpaca first (catches native bracket exits), then check
    remaining open positions for manual trail/stop/target/bonus logic.
    Returns summary of actions taken.
    """
    _reconcile_with_alpaca()
    positions = open_positions()
    if not positions:
        return {"checked": 0, "closed": []}

    today_realized = sum(
        r.get("realized_pnl") or 0
        for r in db.select("b_positions", filters={"status": "CLOSED"})
        if str(r.get("closed_at", ""))[:10] == str(date.today())
    )

    # Batch-fetch VWAP signals once for all open tickers
    tickers = [p["ticker"] for p in positions if p.get("ticker")]
    intraday_signals = get_intraday_signals(tickers) if VWAP_EXIT_ENABLED and tickers else {}

    closed = []
    for pos in positions:
        ticker = pos["ticker"]
        price  = get_current_price(ticker)
        if price is None:
            continue

        entry    = float(pos["entry_price"])
        target   = float(pos["target_price"])
        stop     = float(pos["stop_loss"])
        shares   = int(pos["shares"])
        watermark     = float(pos.get("high_watermark") or entry)
        low_watermark = float(pos.get("low_watermark")  or entry)

        unrealized    = round(shares * (price - entry), 2)
        new_watermark = max(watermark, price)
        new_low_wm    = min(low_watermark, price)

        # ── R-multiple stop ladder ────────────────────────────────────────────
        # R = initial risk per share (entry − original stop).
        # +1R profit → move stop to entry (breakeven, capital protected).
        # +2R profit → move stop to entry + R (lock in half the move).
        # Stop only ever ratchets up — never back down.
        if R_LADDER_ENABLED:
            R = entry - float(pos.get("stop_loss") or stop)
            if R > 0:
                if price >= entry + 2 * R and stop < round(entry + R, 2):
                    new_stop = round(entry + R, 2)
                    db.update("b_positions", {"id": pos["id"]}, {"stop_loss": new_stop})
                    stop = new_stop
                    print(f"  📈 R-ladder +2R: {ticker} stop → ${new_stop:.2f} (entry+R)")
                elif price >= entry + R and stop < entry:
                    new_stop = round(entry, 2)
                    db.update("b_positions", {"id": pos["id"]}, {"stop_loss": new_stop})
                    stop = new_stop
                    print(f"  📈 R-ladder +1R: {ticker} stop → breakeven ${new_stop:.2f}")

        # Update watermarks and current price
        db.update("b_positions", {"id": pos["id"]}, {
            "current_price":  price,
            "unrealized_pnl": unrealized,
            "high_watermark": new_watermark,
            "low_watermark":  new_low_wm,
        })

        close_reason = None

        # Bonus target — close everything to protect exceptional day
        if (today_realized + unrealized) >= DAILY_BONUS_TARGET:
            close_reason = "BONUS_TARGET"

        # Target hit
        elif price >= target:
            close_reason = "TARGET"

        # VWAP break — exit if price drops below VWAP while capital is at risk.
        # Skip when stop >= entry (R-ladder already protects capital — let it ride).
        elif VWAP_EXIT_ENABLED and stop < entry:
            sig  = intraday_signals.get(ticker, {})
            vwap = sig.get("vwap")
            if vwap and price < vwap:
                close_reason = "VWAP_BREAK"
                print(f"  📉 VWAP break: {ticker} ${price:.2f} < VWAP ${vwap:.2f} — exiting")

        # Hard stop — safety net; native trail bracket should fire first
        if not close_reason and price <= stop:
            close_reason = "STOP"

        if close_reason:
            _close_position(pos, price, close_reason)
            closed.append({"ticker": ticker, "reason": close_reason, "pnl": unrealized})

    return {"checked": len(positions), "closed": closed}


def close_all_positions(reason: str = "EOD") -> list[dict]:
    """Close all open positions at market. Called at EOD."""
    positions = open_positions()
    closed    = []
    for pos in positions:
        price = get_current_price(pos["ticker"]) or float(pos.get("current_price") or pos["entry_price"])
        _close_position(pos, price, reason)
        closed.append(pos["ticker"])

    # Safety sweep: close any Alpaca positions orphaned (filled but never tracked in DB)
    db_tickers = {p["ticker"] for p in positions}
    try:
        broker = _get()
        broker.cancel_orders_for_symbol  # check connection
        for ap in broker.get_all_positions():
            if ap.symbol not in db_tickers:
                print(f"[alpaca] Orphan sweep closing {ap.symbol} ({ap.qty} shares)")
                try:
                    broker.close_position(ap.symbol)
                    closed.append(ap.symbol)
                except Exception as e:
                    print(f"[alpaca] Could not close orphan {ap.symbol}: {e}")
    except Exception as e:
        print(f"[alpaca] Orphan sweep error: {e}")

    print(f"[alpaca] EOD closed: {closed}")
    return closed


def _close_position(pos: dict, price: float, reason: str) -> None:
    ticker  = pos["ticker"]
    shares  = int(pos["shares"])
    entry   = float(pos["fill_price"] or pos["entry_price"])  # use actual fill price for P&L
    pnl     = round(shares * (price - entry), 2)

    # Cancel open bracket legs before submitting manual close —
    # stop/take-profit legs conflict with the market sell order.
    order_id = pos.get("alpaca_order_id")
    if order_id:
        try:
            _get().cancel_order_by_id(order_id)
            time.sleep(0.3)
        except Exception as e:
            print(f"  ⚠️  {ticker}: bracket cancel failed ({e}) — proceeding with market close anyway")

    high_wm = float(pos.get("high_watermark") or entry)
    low_wm  = float(pos.get("low_watermark")  or entry)
    mae     = round(max(0.0, (entry - low_wm)  * shares), 2)  # dollars against us
    mfe     = round(max(0.0, (high_wm - entry) * shares), 2)  # dollars in our favour

    close_confirmed = False
    actual_close_price = price
    try:
        broker = _get()
        req = MarketOrderRequest(
            symbol=ticker,
            qty=shares,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            client_order_id=_order_id(f"{ticker}_exit"),
        )
        close_order = broker.submit_order(req)

        # Verify the sell actually filled before marking CLOSED in DB.
        # Use 15 attempts (15s) — EOD market sells can lag at 3:55 PM before flush.
        for attempt in range(15):
            time.sleep(1)
            try:
                result = broker.get_order_by_id(str(close_order.id))
                status = str(result.status).lower()
                if status == "filled":
                    if result.filled_avg_price:
                        actual_close_price = float(result.filled_avg_price)
                    close_confirmed = True
                    break
                elif status in ("cancelled", "rejected", "expired"):
                    print(f"[alpaca] ⚠️ ALERT: Close order for {ticker} {status} — position stays OPEN")
                    return  # do NOT mark as closed in DB
            except Exception:
                pass

        if not close_confirmed:
            print(f"[alpaca] ⚠️ Could not confirm close for {ticker} after 15s — position stays OPEN in DB")
            return

    except Exception as e:
        print(f"[alpaca] Close order failed for {ticker}: {e}")
        return  # don't update DB if order submission failed

    price = actual_close_price
    pnl   = round(shares * (price - entry), 2)
    db.update("b_positions", {"id": pos["id"]}, {
        "status":         "CLOSED",
        "close_price":    price,
        "realized_pnl":   pnl,
        "close_reason":   reason,
        "closed_at":      datetime.utcnow().isoformat(),
        "exit_mechanism": reason,
        "mae":            mae,
        "mfe":            mfe,
    })
    print(f"[alpaca] Closed {ticker} @ ${price:.2f} — P&L ${pnl:.2f} ({reason})")
