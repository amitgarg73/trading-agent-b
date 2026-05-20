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
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus
from core import db
from config.settings import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, STRATEGY_TAG,
    TRAIL_PCT, PARTIAL_PROFIT_ENABLED, PARTIAL_PROFIT_PCT,
    DAILY_LOCK_IN_TARGET, DAILY_BONUS_TARGET, LOCK_IN_TRAIL_PCT,
)

_client: TradingClient | None = None


def _get() -> TradingClient:
    global _client
    if _client is None:
        _client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
    return _client


def _order_id(ticker: str) -> str:
    """Unique client order ID with strategy tag for easy filtering."""
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"strat{STRATEGY_TAG}_{ticker}_{ts}"


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


def place_orders(trades: list[dict]) -> list[dict]:
    """Place market buy orders for approved trades. Returns list with order_ids."""
    broker  = _get()
    placed  = []

    for trade in trades:
        ticker = trade["ticker"]
        shares = int(trade["shares"])
        pool   = trade.get("pool", 2)

        try:
            req = MarketOrderRequest(
                symbol=ticker,
                qty=shares,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                client_order_id=_order_id(ticker),
            )
            order = broker.submit_order(req)
            print(f"[alpaca] BUY {shares} {ticker} @ market (pool {pool}) — order {order.id}")

            # Write to b_positions
            db.insert("b_positions", {
                "ticker":          ticker,
                "pool":            pool,
                "action":          "BUY",
                "entry_price":     trade["entry_price"],
                "target_price":    trade["target_price"],
                "stop_loss":       trade["stop_loss"],
                "shares":          shares,
                "position_size":   trade["position_size"],
                "status":          "OPEN",
                "alpaca_order_id": str(order.id),
                "high_watermark":  trade["entry_price"],
            })

            placed.append({**trade, "alpaca_order_id": str(order.id)})
            time.sleep(0.3)

        except Exception as e:
            print(f"[alpaca] Failed to place {ticker}: {e}")

    return placed


def open_positions() -> list[dict]:
    return db.select("b_positions", filters={"status": "OPEN"})


def update_positions_intraday() -> dict:
    """
    Check all open positions against current prices.
    Apply trailing stop, partial profit, lock-in logic.
    Returns summary of actions taken.
    """
    positions = open_positions()
    if not positions:
        return {"checked": 0, "closed": []}

    today_realized = sum(
        r.get("realized_pnl") or 0
        for r in db.select("b_positions", filters={"status": "CLOSED"})
        if str(r.get("closed_at", ""))[:10] == str(date.today())
    )

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
        watermark = float(pos.get("high_watermark") or entry)

        unrealized = round(shares * (price - entry), 2)
        new_watermark = max(watermark, price)

        # Update watermark
        db.update("b_positions", {"id": pos["id"]}, {
            "current_price":  price,
            "unrealized_pnl": unrealized,
            "high_watermark": new_watermark,
        })

        close_reason = None

        # Bonus target — close everything
        if (today_realized + unrealized) >= DAILY_BONUS_TARGET:
            close_reason = "BONUS_TARGET"

        # Target hit
        elif price >= target:
            close_reason = "TARGET"

        # Trailing stop
        elif price <= new_watermark * (1 - TRAIL_PCT):
            close_reason = "MANUAL_TRAIL"

        # Hard stop
        elif price <= stop:
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
    print(f"[alpaca] EOD closed: {closed}")
    return closed


def _close_position(pos: dict, price: float, reason: str) -> None:
    ticker  = pos["ticker"]
    shares  = int(pos["shares"])
    entry   = float(pos["entry_price"])
    pnl     = round(shares * (price - entry), 2)

    try:
        broker = _get()
        req = MarketOrderRequest(
            symbol=ticker,
            qty=shares,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            client_order_id=_order_id(f"{ticker}_exit"),
        )
        broker.submit_order(req)
    except Exception as e:
        print(f"[alpaca] Close order failed for {ticker}: {e}")

    db.update("b_positions", {"id": pos["id"]}, {
        "status":       "CLOSED",
        "close_price":  price,
        "realized_pnl": pnl,
        "close_reason": reason,
        "closed_at":    datetime.utcnow().isoformat(),
        "exit_mechanism": reason,
    })
    print(f"[alpaca] Closed {ticker} @ ${price:.2f} — P&L ${pnl:.2f} ({reason})")
