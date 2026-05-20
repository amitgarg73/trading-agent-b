"""
Orchestrator — Strategy B entry point.
Called by GitHub Actions: python orchestrator.py --mode premarket|intraday|eod
"""
import sys
import argparse
from datetime import date, datetime

from scanner.scanner import run_scan
from scanner.pool_filter import get_pool3_tickers, get_pool3_with_context
from agents import strategy, risk, guardrails, market_context
from agents.alpaca_broker import place_orders, update_positions_intraday, close_all_positions, open_positions
from agents.pool_scorer import score_today, write_daily_performance
from core import db
from core.pool_manager import seed_pools_if_empty


def _is_trading_day() -> bool:
    """Return False on weekends and NYSE holidays using Alpaca's calendar."""
    if date.today().weekday() >= 5:
        return False
    try:
        from alpaca.trading.client import TradingClient
        from config.settings import ALPACA_API_KEY, ALPACA_SECRET_KEY
        client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
        cal = client.get_calendar(start=str(date.today()), end=str(date.today()))
        return len(cal) > 0
    except Exception:
        return True  # fail open — don't block trading if calendar check fails


def _is_halted() -> bool:
    rows = db.select("b_trade_plans", filters={"status": "HALTED"})
    if rows and rows[0].get("date") == str(date.today()):
        print("🛑 Strategy B halted for today")
        return True
    return False


def premarket(broker: str = "alpaca") -> None:
    print(f"\n{'='*60}")
    print(f"  STRATEGY B — PREMARKET — {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    print(f"{'='*60}\n")

    if not _is_trading_day():
        print(f"[orchestrator] {date.today()} is not a NYSE trading day — skipping")
        return

    # Prevent double-run
    existing = db.select("b_trade_plans", filters={"date": str(date.today())})
    if existing:
        print("[orchestrator] Premarket already ran today — skipping")
        return

    if _is_halted():
        return

    seed_pools_if_empty()

    # 1. Select Pool 3 for today
    print("\n[1] Selecting Pool 3 (today's elite picks)...")
    pool3_context = get_pool3_with_context()
    pool3_tickers = [m["ticker"] for m in pool3_context]
    print(f"    Pool 3: {pool3_tickers}")

    # Cap candidates to what available capital can fund
    from config.settings import TOTAL_CAPITAL, POSITION_SIZE_BY_CONFIDENCE
    _open_b     = db.select("b_positions", filters={"status": "OPEN"})
    _deployed_b = sum(float(p.get("position_size") or 0) for p in _open_b)
    _available_b = TOTAL_CAPITAL - _deployed_b
    _min_size_b  = min(POSITION_SIZE_BY_CONFIDENCE.values())
    _capital_cap_b = max(0, int(_available_b // _min_size_b))
    if len(pool3_tickers) > _capital_cap_b:
        pool3_context = pool3_context[:_capital_cap_b]
        pool3_tickers = pool3_tickers[:_capital_cap_b]
        print(f"    Capital cap: trimmed to {_capital_cap_b} candidates "
              f"(${_available_b:,.0f} available / ${_min_size_b:,} min size)")
    else:
        print(f"    Capital available: ${_available_b:,.0f} → fits all {len(pool3_tickers)} candidates")

    if not pool3_tickers:
        print("[orchestrator] No Pool 3 candidates — skipping today")
        return

    # 2. Get market context
    print("\n[2] Fetching market context...")
    mkt = market_context.get()

    # 3. Scan Pool 3 tickers
    print(f"\n[3] Scanning {len(pool3_tickers)} Pool 3 tickers...")
    candidates = run_scan(pool3_tickers)
    print(f"    {len(candidates)} candidates after scan")

    # 4. Claude selects trades
    print("\n[4] Claude selecting trades...")
    result = strategy.select_trades(candidates, mkt, pool3_context)
    trades = result.get("trades", [])

    # 5. Risk validation
    print("\n[5] Risk validation...")
    approved, risk_rejected = risk.validate(trades)

    # 6. Guardrails
    print("\n[6] Guardrails check...")
    final, guard_rejected = guardrails.check(approved, broker=broker)

    # If risk killed everything (e.g. daily loss limit), record a HALTED plan and stop
    if not final and risk_rejected:
        halt_reason = "; ".join(risk_rejected)
        db.insert("b_trade_plans", {
            "date":            str(date.today()),
            "market_context":  str(mkt),
            "pool3_tickers":   pool3_tickers,
            "risk_note":       halt_reason,
            "status":          "HALTED",
        })
        print(f"🛑 No trades placed — {halt_reason}")
        return

    # 7. Save plan
    plan_id = None
    if final or trades:
        plan_row = db.insert("b_trade_plans", {
            "date":                   str(date.today()),
            "market_context":         str(mkt),
            "pool3_tickers":          pool3_tickers,
            "total_estimated_profit": sum(t.get("estimated_profit", 0) for t in final),
            "risk_note":              f"Rejected: {risk_rejected + guard_rejected}",
            "status":                 "ACTIVE",
        })
        plan_id = plan_row["id"]

        for t in final:
            db.insert("b_planned_trades", {
                "plan_id":         plan_id,
                "ticker":          t["ticker"],
                "pool":            t.get("pool", 2),
                "action":          t["action"],
                "entry_price":     t["entry_price"],
                "target_price":    t["target_price"],
                "stop_loss":       t["stop_loss"],
                "position_size":   t["position_size"],
                "shares":          t["shares"],
                "estimated_profit": t.get("estimated_profit"),
                "confidence":      t["confidence"],
                "reasoning":       t.get("reasoning", ""),
                "status":          "PLANNED",
            })

    # 8. Place orders
    if final and broker == "alpaca":
        print(f"\n[7] Placing {len(final)} orders via Alpaca...")
        placed = place_orders(final)
        print(f"    Placed: {[p['ticker'] for p in placed]}")
    elif final:
        print(f"\n[7] Simulation mode — would trade: {[t['ticker'] for t in final]}")

    print(f"\n✅ Premarket complete — {len(final)} trades | "
          f"Est. profit: ${sum(t.get('estimated_profit',0) for t in final):.0f}")


def intraday(broker: str = "alpaca") -> None:
    print(f"\n{'='*60}")
    print(f"  STRATEGY B — INTRADAY — {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    print(f"{'='*60}\n")

    if _is_halted():
        return

    positions = open_positions()
    if not positions:
        print("[orchestrator] No open positions to manage")
        return

    print(f"[orchestrator] Managing {len(positions)} open positions...")
    if broker == "alpaca":
        result = update_positions_intraday()
        print(f"[orchestrator] Intraday: checked {result['checked']}, "
              f"closed {len(result['closed'])}: {result['closed']}")
    else:
        print("[orchestrator] Simulation mode — intraday check only")
        for p in positions:
            print(f"  {p['ticker']} | entry ${p['entry_price']} | "
                  f"target ${p['target_price']} | stop ${p['stop_loss']}")


def eod(broker: str = "alpaca") -> None:
    print(f"\n{'='*60}")
    print(f"  STRATEGY B — EOD — {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    print(f"{'='*60}\n")

    # 1. Close all open positions
    print("[1] Closing all open positions...")
    if broker == "alpaca":
        closed = close_all_positions(reason="EOD")
    else:
        print("    Simulation mode — skipping close")
        closed = []

    # 2. Score today's stocks + update pool membership
    print("\n[2] Running pool scorer...")
    scoring = score_today()
    print(f"    Scored {scoring['scored']} stocks | "
          f"Promoted: {scoring['promoted']} | Demoted: {scoring['demoted']}")

    # 3. Write daily performance
    print("\n[3] Writing daily performance...")
    write_daily_performance()

    print(f"\n✅ EOD complete — closed {len(closed)} position(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategy B Orchestrator")
    parser.add_argument("--mode",   required=True, choices=["premarket", "intraday", "eod"])
    parser.add_argument("--broker", default="alpaca", choices=["alpaca", "simulation"])
    args = parser.parse_args()

    if args.mode == "premarket":
        premarket(args.broker)
    elif args.mode == "intraday":
        intraday(args.broker)
    elif args.mode == "eod":
        eod(args.broker)
