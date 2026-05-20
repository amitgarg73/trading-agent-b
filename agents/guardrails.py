"""
Guardrails — final sanity checks before order placement.
Rejects trades with obviously wrong prices, missing fields, or calculation errors.

Checks applied in order:
  1. Required fields
  2. Action whitelist — BUY only
  3. Formula validation — target and stop must match settings formulas
  4. R:R minimum
  5. Live price sanity — entry must be within PRICE_SANITY_PCT of current market price
  6. Duplicate guard — ticker not already open or traded today
  7. Buying power — Alpaca account has enough capital for the full batch
"""
from __future__ import annotations
from datetime import date
import yfinance as yf
from config.settings import TARGET_PCT, MAX_LOSS_PER_TRADE, MIN_REWARD_RISK, PRICE_SANITY_PCT, TOTAL_CAPITAL
from core import db


REQUIRED_FIELDS = ["ticker", "action", "entry_price", "target_price",
                   "stop_loss", "shares", "position_size", "confidence"]


def _current_price(ticker: str) -> float | None:
    """Fetch live price — Alpaca first, yfinance fallback."""
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest
        import os
        data = StockHistoricalDataClient(
            os.environ.get("ALPACA_API_KEY", ""),
            os.environ.get("ALPACA_SECRET_KEY", ""),
        )
        req   = StockLatestTradeRequest(symbol_or_symbols=ticker)
        trade = data.get_stock_latest_trade(req)
        return float(trade[ticker].price)
    except Exception:
        pass
    try:
        df = yf.Ticker(ticker).history(period="1d", interval="1m")
        if not df.empty:
            return round(float(df["Close"].iloc[-1]), 2)
    except Exception:
        pass
    return None


def _get_buying_power(broker: str) -> float | None:
    if broker != "alpaca":
        return float(TOTAL_CAPITAL)
    try:
        from alpaca.trading.client import TradingClient
        import os
        client = TradingClient(
            os.environ.get("ALPACA_API_KEY", ""),
            os.environ.get("ALPACA_SECRET_KEY", ""),
            paper=True,
        )
        return float(client.get_account().buying_power)
    except Exception:
        return None


def _traded_today() -> set[str]:
    today = date.today().isoformat()
    open_pos    = db.select("b_positions", filters={"status": "OPEN"})
    closed_pos  = db.select("b_positions", filters={"status": "CLOSED"})
    closed_today = [p for p in closed_pos if str(p.get("closed_at", ""))[:10] == today]
    return {p["ticker"] for p in open_pos} | {p["ticker"] for p in closed_today}


def check(trades: list[dict], broker: str = "alpaca") -> tuple[list[dict], list[str]]:
    """Returns (passed_trades, rejection_reasons)."""
    if not trades:
        return [], []

    already_traded = _traded_today()
    buying_power   = _get_buying_power(broker)
    committed      = 0.0
    passed, rejected = [], []

    for t in trades:
        ticker = t.get("ticker", "?")
        reason = _validate(t, already_traded, buying_power, committed)
        if reason:
            rejected.append(f"{ticker}: {reason}")
            print(f"[guardrails] Blocked {ticker}: {reason}")
        else:
            passed.append(t)
            committed += float(t.get("position_size", 0))
            already_traded.add(ticker)

    return passed, rejected


def _validate(trade: dict, already_traded: set[str],
              buying_power: float | None, committed: float) -> str | None:
    ticker = trade.get("ticker", "?")

    # Required fields
    for f in REQUIRED_FIELDS:
        if trade.get(f) is None:
            return f"missing field: {f}"

    # Action whitelist
    if trade.get("action") != "BUY":
        return f"action {trade.get('action')} not permitted — BUY only"

    entry  = float(trade["entry_price"])
    target = float(trade["target_price"])
    stop   = float(trade["stop_loss"])
    shares = int(trade["shares"])
    ps     = float(trade["position_size"])

    if entry <= 0:
        return "entry_price <= 0"
    if shares <= 0:
        return "shares <= 0"
    if target <= entry:
        return f"target {target} <= entry {entry}"
    if stop >= entry:
        return f"stop {stop} >= entry {entry}"

    # Formula validation
    expected_target = round(entry * (1 + TARGET_PCT), 2)
    if abs(target - expected_target) / expected_target > 0.02:
        return f"target {target} deviates from formula {expected_target}"

    expected_stop = round(entry * (1 - MAX_LOSS_PER_TRADE), 2)
    if abs(stop - expected_stop) / expected_stop > 0.02:
        return f"stop {stop} deviates from formula {expected_stop}"

    # R:R
    profit = shares * (target - entry)
    loss   = shares * (entry - stop)
    if loss <= 0:
        return "max_loss is zero or negative"
    if profit / loss < MIN_REWARD_RISK:
        return f"R:R {profit/loss:.2f} < minimum {MIN_REWARD_RISK}"

    # Duplicate guard — open or already traded today
    if ticker in already_traded:
        return f"duplicate: {ticker} already open or traded today"

    # Live price sanity — fail closed if price unavailable
    market_price = _current_price(ticker)
    if market_price is None:
        return "price sanity: could not fetch live price — blocking to avoid stale entry"
    deviation = abs(entry - market_price) / market_price
    if deviation > PRICE_SANITY_PCT:
        return (f"price sanity: entry ${entry:.2f} is {deviation*100:.1f}% "
                f"from market ${market_price:.2f} (max {PRICE_SANITY_PCT*100:.0f}%)")

    # Buying power check — cumulative across batch
    if buying_power is not None:
        remaining = buying_power - committed
        if ps > remaining:
            return (f"insufficient capital: need ${ps:,.0f} but only "
                    f"${remaining:,.0f} remaining of ${buying_power:,.0f}")

    return None
