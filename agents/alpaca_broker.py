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


def get_live_quotes(tickers: list[str]) -> dict[str, dict]:
    """Return {ticker: {"ask": float, "bid": float}} for order-time pricing."""
    if not tickers:
        return {}
    try:
        from alpaca.data.requests import StockLatestQuoteRequest
        quotes = _dclient().get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=tickers)
        )
        result = {}
        for ticker, quote in quotes.items():
            ask = getattr(quote, "ask_price", None)
            bid = getattr(quote, "bid_price", None)
            if ask and bid and float(ask) > 0 and float(bid) > 0:
                result[ticker] = {"ask": round(float(ask), 4), "bid": round(float(bid), 4)}
        return result
    except Exception as e:
        print(f"        ⚠️  Live quote fetch failed: {e}")
        return {}


def hybrid_limit_price(ask: float, bid: float) -> float | None:
    """
    Passive-first limit price for a BUY — set below the current ask so the
    stock has to come to us rather than us chasing it.

      spread < 0.10%  → bid: ultra-tight market, fills within seconds on any normal tick
      spread 0.10–0.20% → mid: moderate spread, mid fills on normal intraday dips
      spread > 0.20%  → None (skip): spread destroys R:R before entry
    """
    if ask <= 0 or bid <= 0 or ask < bid:
        return round(ask, 2) if ask > 0 else None
    spread_pct = (ask - bid) / ask
    if spread_pct > 0.002:
        return None
    if spread_pct < 0.001:
        return round(bid, 2)                    # was mid — now bid
    return round((ask + bid) / 2, 2)            # was ask — now mid


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
            day_high  = float(getattr(snap.daily_bar, "high", 0) or 0)
            day_low   = float(getattr(snap.daily_bar, "low",  0) or 0)
            signals[ticker] = {
                "above_vwap":       price > vwap,
                "vwap":             round(vwap, 2),
                "today_pct_change": round(today_pct * 100, 2),
                "rs_vs_spy":        rs_vs_spy,
                "day_high":         round(day_high, 2),
                "day_low":          round(day_low, 2),
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
    """Place limit buy orders for approved trades. Returns list with order_ids."""
    broker  = _get()
    placed  = []

    for trade in trades:
        ticker = trade["ticker"]
        shares = int(trade["shares"])
        pool   = trade.get("pool", 2)

        try:
            # Fetch live bid/ask at submission time and compute best limit price.
            # Tight spread (<0.1%) → bid; moderate (0.1–0.2%) → mid; wide (>0.2%) → plan price.
            qt = get_live_quotes([ticker]).get(ticker)
            plan_ask = float(trade["entry_price"])
            if qt:
                limit_px = hybrid_limit_price(qt["ask"], qt["bid"])
                if limit_px is None:
                    spread_pct = (qt["ask"] - qt["bid"]) / qt["ask"] * 100
                    if spread_pct > 5.0:
                        # Extreme spread — quote data is unreliable (IEX stale, halt, etc.).
                        # Cannot safely set stop; skip rather than risk immediate stop trigger.
                        print(f"[alpaca] {ticker} extreme spread {spread_pct:.2f}% — skipping (bad quote data)")
                        continue
                    # Moderate wide spread (0.2–5%) — use plan price as limit but anchor
                    # stop/target to the live bid. BUY limits fill at market price (≤ limit),
                    # so the fill can land anywhere down to bid. Anchoring to bid guarantees
                    # stop < fill and prevents the bracket from firing immediately on entry.
                    plan_stop_pct   = (trade["entry_price"] - float(trade["stop_loss"]))   / trade["entry_price"]
                    plan_target_pct = (float(trade["target_price"]) - trade["entry_price"]) / trade["entry_price"]
                    entry_price  = round(plan_ask, 2)
                    stop_price   = round(qt["bid"] * (1 - plan_stop_pct), 2)
                    target_price = round(qt["bid"] * (1 + plan_target_pct), 2)
                    print(f"[alpaca] {ticker} wide spread {spread_pct:.2f}% — limit={entry_price:.2f} stop={stop_price:.2f} (bid-anchored)")
                else:
                    # Anchor stop/target to the live limit price (not the stale plan price).
                    plan_stop_pct   = (trade["entry_price"] - float(trade["stop_loss"]))   / trade["entry_price"]
                    plan_target_pct = (float(trade["target_price"]) - trade["entry_price"]) / trade["entry_price"]
                    entry_price  = limit_px
                    stop_price   = round(limit_px * (1 - plan_stop_pct), 2)
                    target_price = round(limit_px * (1 + plan_target_pct), 2)
                    if limit_px < qt["ask"]:
                        saved = round((qt["ask"] - limit_px) * shares, 2)
                        print(f"[alpaca] {ticker} mid-price limit: ask={qt['ask']:.2f} limit={limit_px:.2f} saves ~${saved:.2f}")
            else:
                entry_price  = round(plan_ask, 2)
                stop_price   = round(float(trade["stop_loss"]), 2)
                target_price = round(float(trade["target_price"]), 2)

            req = LimitOrderRequest(
                symbol=ticker,
                qty=shares,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                limit_price=entry_price,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=target_price),
                stop_loss=StopLossRequest(stop_price=stop_price),
                client_order_id=_order_id(ticker),
            )
            order = broker.submit_order(req)
            print(f"[alpaca] BUY {shares} {ticker} @ limit {entry_price} → target={target_price} stop={stop_price} (pool {pool}) — order {order.id}")

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
                # Order still pending — write to DB so intraday monitoring tracks it.
                # Passive bid/mid limit orders can take minutes to fill; 15s is not enough.
                # Trail will be submitted on the next intraday cycle once fill is confirmed.
                print(f"[alpaca] {ticker} limit pending after 15s — recording for intraday reconcile")

            if fill_price:
                slippage_bps = round(abs(fill_price - entry_price) / entry_price * 10_000, 1)
                print(f"[alpaca] {ticker} fill=${fill_price:.2f} limit=${entry_price:.2f} slip={slippage_bps}bps")

            trail_order_id = None
            if order_accepted and USE_NATIVE_TRAILING_STOP:
                trail_order_id = submit_trailing_stop(ticker=ticker, shares=shares, trail_pct=TRAIL_PCT)
                if trail_order_id:
                    print(f"[alpaca] Trail stop active: {ticker} {TRAIL_PCT*100:.1f}% → {trail_order_id}")
                else:
                    print(f"[alpaca] ⚠️  Trail stop failed for {ticker} — bracket hard stop only")

            pos_row = {
                "ticker":          ticker,
                "pool":            pool,
                "action":          "BUY",
                "entry_price":     entry_price,
                "fill_price":      fill_price,
                "target_price":    target_price,
                "stop_loss":       stop_price,
                "shares":          shares,
                "position_size":   trade["position_size"],
                "status":          "OPEN",
                "alpaca_order_id": str(order.id),
                "high_watermark":  fill_price or entry_price,
                "low_watermark":   fill_price or entry_price,
                "run_id":          run_id,
            }
            if trail_order_id:
                pos_row["trail_order_id"] = trail_order_id
            # Write to b_positions
            db.insert("b_positions", pos_row)

            placed.append({**trade, "alpaca_order_id": str(order.id)})
            time.sleep(0.3)

        except Exception as e:
            import traceback
            print(f"[alpaca] Failed to place {ticker}: {e}")
            traceback.print_exc()

    return placed


def submit_trailing_stop(ticker: str, shares: int, trail_pct: float) -> str | None:
    from alpaca.trading.requests import TrailingStopOrderRequest
    try:
        req = TrailingStopOrderRequest(
            symbol=ticker, qty=shares, side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            trail_percent=round(trail_pct * 100, 2),
            client_order_id=_order_id(ticker),
        )
        order = _get().submit_order(req)
        return str(order.id)
    except Exception as e:
        err = str(e)
        if "insufficient qty" in err or "40310000" in err:
            # Bracket legs are still holding the shares — bracket covers this position.
            print(f"  [trail] {ticker} shares held by bracket legs — trail skipped, bracket covers")
        else:
            print(f"  [trail] ⚠️  Trail submission failed for {ticker}: {e}")
        return None


def cancel_order(order_id: str) -> None:
    try:
        _get().cancel_order_by_id(order_id)
    except Exception:
        pass


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
        from datetime import timezone
        today_start = datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
        )
        all_orders = _get().get_orders(
            GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500, after=today_start)
        )
        today = datetime.utcnow().date().isoformat()
        filled_buys = {
            str(o.symbol)
            for o in all_orders
            if getattr(o.side, "value", str(o.side)) == "buy"
            and getattr(o.status, "value", str(o.status)) == "filled"
            and str(o.filled_at or o.submitted_at or "").startswith(today[:10])
        }
        trail_orders_filled = {
            str(o.id): float(o.filled_avg_price)
            for o in all_orders
            if getattr(o.side, "value", str(o.side)) == "sell"
            and "trailing" in str(getattr(o, "order_type", "") or "").lower()
            and getattr(o.status, "value", str(o.status)) == "filled"
            and o.filled_avg_price
        }
        pending_buys = {
            str(o.symbol)
            for o in all_orders
            if getattr(o.side, "value", str(o.side)) == "buy"
            and getattr(o.status, "value", str(o.status)) in
                ("pending_new", "accepted", "new", "held", "partially_filled")
            and str(o.submitted_at or "").startswith(today[:10])
        }
    except Exception as e:
        print(f"  ⚠️  Reconciliation: order fetch failed — {e}")
        from core import ledger, alerts
        ledger.log("reconcile_failed", {"error": str(e)})
        db.insert("b_scan_results", {
            "date":      datetime.utcnow().date().isoformat(),
            "scan_type": "reconcile_failed",
            "results":   {"error": str(e), "ts": datetime.utcnow().isoformat()},
        })
        alerts.send_alert(
            "Strategy B: Reconciliation Failed",
            f"Order fetch exception: {e}\nCycle skipped — unfilled orders undetected this cycle.",
        )
        return

    for pos in positions:
        if pos["ticker"] in alpaca_tickers:
            continue
        if pos["ticker"] in pending_buys:
            print(f"  ⏳ Reconciliation: {pos['ticker']} buy order pending — waiting for fill")
            continue
        # Check standalone trail exit before bracket — trail fires in real-time server-side.
        trail_id = pos.get("trail_order_id")
        if trail_id and trail_id in trail_orders_filled:
            close_price = trail_orders_filled[trail_id]
            entry  = float(pos.get("fill_price") or pos["entry_price"])
            shares = int(pos["shares"])
            pnl    = round(shares * (close_price - entry), 2)
            cancel_order(pos.get("alpaca_order_id", ""))  # cancel remaining bracket legs
            db.update("b_positions", {"id": pos["id"]}, {
                "status":         "CLOSED",
                "close_reason":   "NATIVE_TRAIL",
                "exit_reason":    "NATIVE_TRAIL",
                "exit_mechanism": "NATIVE_TRAIL",
                "close_price":    close_price,
                "exit_price":     close_price,
                "realized_pnl":   pnl,
                "closed_at":      datetime.utcnow().isoformat(),
            })
            print(f"  🔒 Native trail exit: {pos['ticker']} @ ${close_price:.2f} P&L={pnl:+.2f}")
            continue

        if pos["ticker"] in filled_buys:
            # Entry filled — bracket leg may have fired. Resolve it here if position is gone.
            order_id = pos.get("alpaca_order_id")
            if order_id:
                close_price, mechanism = get_order_fill(order_id)
                if close_price and pos.get("trail_order_id"):
                    cancel_order(pos["trail_order_id"])  # bracket exited — cancel orphaned trail
                if close_price:
                    entry  = float(pos.get("fill_price") or pos["entry_price"])
                    shares = int(pos["shares"])
                    pnl    = round(shares * (close_price - entry), 2)
                    hwm    = float(pos.get("high_watermark") or entry)
                    lwm    = float(pos.get("low_watermark")  or entry)
                    db.update("b_positions", {"id": pos["id"]}, {
                        "status":         "CLOSED",
                        "close_reason":   mechanism or "BRACKET",
                        "exit_reason":    mechanism or "BRACKET",
                        "exit_mechanism": mechanism or "BRACKET",
                        "close_price":    close_price,
                        "exit_price":     close_price,
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
            "exit_reason":    "UNFILLED",
            "exit_mechanism": "UNFILLED",
            "closed_at":      datetime.utcnow().isoformat(),
            "realized_pnl":   0,
            "close_price":    pos.get("entry_price"),
            "exit_price":     pos.get("entry_price"),
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

    # Backfill trailing stops for confirmed-filled positions that have none.
    # Bracket legs are TimeInForce.DAY — they expire at EOD. Multi-day positions
    # lose both bracket and trail protection after day 1. Detect and resubmit here.
    if USE_NATIVE_TRAILING_STOP:
        alpaca_open = get_open_tickers()
        for pos in positions:
            if (pos.get("trail_order_id") is None
                    and pos.get("fill_price") is not None
                    and pos["ticker"] in alpaca_open):
                trail_id = submit_trailing_stop(pos["ticker"], int(pos["shares"]), TRAIL_PCT)
                if trail_id:
                    db.update("b_positions", {"id": pos["id"]}, {"trail_order_id": trail_id})
                    print(f"  [trail] Backfilled trail for {pos['ticker']}: {trail_id[:8]}")

    today_realized = sum(
        r.get("realized_pnl") or 0
        for r in db.select("b_positions", filters={"status": "CLOSED"},
                           filters_gte={"closed_at": f"{date.today()}T00:00:00"})
        if r.get("close_reason") not in ("CLEANUP", "UNFILLED")
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

        # Manual trailing stop fallback — only when no native trail is active.
        # Mirrors Strategy A's high-watermark tracking. Fires if price pulls back
        # from the session peak by more than TRAIL_PCT, provided it's above the
        # hard stop (hard stop handles the floor; this handles the trailing exit).
        if not close_reason and not pos.get("trail_order_id"):
            eff_stop = max(stop, round(new_watermark * (1 - TRAIL_PCT), 4))
            if price <= eff_stop and price > stop:
                close_reason = "MANUAL_TRAIL"
                print(f"  📉 Manual trail: {ticker} ${price:.2f} ≤ eff_stop ${eff_stop:.2f} (peak ${new_watermark:.2f})")

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

    # Safety sweep: close Strategy B orphans only (in Alpaca but not in our DB).
    # Filters by STRATEGY_TAG prefix so we never close Strategy A's positions.
    db_tickers = {p["ticker"] for p in positions}
    try:
        broker = _get()
        # Cancel all open orders before sweep — bracket legs hold qty and block market close
        try:
            broker.cancel_orders()
            time.sleep(8)
        except Exception as _ce:
            print(f"[alpaca] cancel_orders in sweep: {_ce}")
        from datetime import timezone, timedelta
        two_days_ago = (datetime.utcnow() - timedelta(days=2)).replace(tzinfo=timezone.utc)
        recent = broker.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.ALL, limit=500, after=two_days_ago
        ))
        our_tickers = {
            str(o.symbol) for o in recent
            if str(o.client_order_id or "").startswith(f"strat{STRATEGY_TAG}_")
        }
        for ap in broker.get_all_positions():
            if ap.symbol not in db_tickers and ap.symbol in our_tickers:
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

    # Cancel open bracket legs and trailing stop before submitting manual close.
    order_id = pos.get("alpaca_order_id")
    if order_id:
        try:
            _get().cancel_order_by_id(order_id)
            time.sleep(0.3)
        except Exception as e:
            print(f"  ⚠️  {ticker}: bracket cancel failed ({e}) — proceeding with market close anyway")
    if pos.get("trail_order_id"):
        cancel_order(pos["trail_order_id"])

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
        "exit_price":     price,
        "realized_pnl":   pnl,
        "close_reason":   reason,
        "exit_reason":    reason,
        "closed_at":      datetime.utcnow().isoformat(),
        "exit_mechanism": reason,
        "mae":            mae,
        "mfe":            mfe,
    })
    print(f"[alpaca] Closed {ticker} @ ${price:.2f} — P&L ${pnl:.2f} ({reason})")
