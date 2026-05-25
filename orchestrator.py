"""
Orchestrator — Strategy B entry point.
Called by GitHub Actions: python orchestrator.py --mode premarket|intraday|eod
"""
from __future__ import annotations
import argparse
from datetime import date, datetime

from scanner.scanner import run_scan
from scanner.pool_filter import get_pool3_tickers, get_pool3_with_context
from agents import strategy, risk, guardrails, market_context, news_intel, sector_guard
from agents.alpaca_broker import place_orders, update_positions_intraday, close_all_positions, open_positions
from agents.pool_scorer import score_today, write_daily_performance
from core import db
from core.alerts import send_alert
from core.pool_manager import seed_pools_if_empty


def _log_run_b(mode: str, status: str, details: dict | None = None) -> None:
    """Write a run-status record to b_scan_results for observability."""
    try:
        db.insert("b_scan_results", {
            "date":       date.today().isoformat(),
            "scan_type":  f"run_{mode}_{status}",
            "scanned_at": datetime.utcnow().isoformat(),
            "candidates": 0,
            "placed":     0,
            "results":    {"mode": mode, "status": status, "ts": datetime.utcnow().isoformat(),
                           **(details or {})},
        })
    except Exception as e:
        print(f"  ⚠️  _log_run_b({mode}, {status}) failed: {e}")
from config.settings import (
    TOTAL_CAPITAL, POSITION_SIZE_BY_CONFIDENCE,
    MAX_POSITIONS, DAILY_BONUS_TARGET, DAILY_LOSS_LIMIT,
    INTRADAY_SCAN_UTC_START, INTRADAY_SCAN_UTC_END, INTRADAY_ENTRY_CUTOFF_UTC,
    INTRADAY_SCAN_MAX_RUNS, INTRADAY_SCAN_MIN_INTERVAL_MINS,
    INTRADAY_TARGET_PCT, MIN_INTRADAY_MOVE_PCT,
    TARGET_PCT, MAX_LOSS_PER_TRADE,
)


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
        return True


def _is_halted() -> bool:
    rows = db.select("b_trade_plans", filters={"status": "HALTED"})
    if rows and rows[0].get("date") == str(date.today()):
        print("🛑 Strategy B halted for today")
        return True
    return False


def _today_realized_pnl() -> float:
    today = str(date.today())
    rows  = db.select("b_positions", filters={"status": "CLOSED"})
    return sum(r.get("realized_pnl") or 0 for r in rows
               if str(r.get("closed_at", ""))[:10] == today)


def _cap_intraday_targets(trades: list[dict]) -> list[dict]:
    """Cap target_price at INTRADAY_TARGET_PCT (1%) for intraday entries."""
    result = []
    for t in trades:
        entry      = float(t["entry_price"])
        max_target = round(entry * (1 + INTRADAY_TARGET_PCT), 2)
        if float(t.get("target_price", 0)) > max_target:
            shares   = int(t.get("shares", 0))
            profit   = round(shares * (max_target - entry), 2)
            stop     = float(t.get("stop_loss", entry))
            max_loss = round(shares * (entry - stop), 2)
            rr       = round(profit / max_loss, 2) if max_loss > 0 else 0
            t = {**t, "target_price": max_target,
                 "estimated_profit": profit, "reward_risk": rr}
        result.append(t)
    return result


def _load_intraday_scans(today: str) -> list[dict]:
    """Load today's intraday scan records. Returns [] if table doesn't exist yet."""
    try:
        rows = db.select("b_scan_results", filters={"date": today, "scan_type": "intraday_scan"})
        return [r.get("results", {}) for r in rows]
    except Exception:
        return []


def _save_intraday_scan(today: str, now_utc: datetime, result: dict) -> None:
    try:
        db.insert("b_scan_results", {
            "date":       today,
            "scan_type":  "intraday_scan",
            "scanned_at": now_utc.isoformat(),
            "candidates": result.get("candidates", 0),
            "placed":     result.get("placed", 0),
            "results":    result,
        })
    except Exception:
        pass  # non-blocking — scan tracking is best-effort


def _maybe_run_intraday_scan(broker: str) -> None:
    """
    Run a mid-day momentum scan on Pool 3 tickers and open new positions if
    stocks are already moving.

    Guards (all must pass):
    - UTC hour in [INTRADAY_SCAN_UTC_START, INTRADAY_SCAN_UTC_END) — 11 AM–2 PM ET
    - Max INTRADAY_SCAN_MAX_RUNS runs per day
    - Min INTRADAY_SCAN_MIN_INTERVAL_MINS minutes since last run
    - Open position slots available (< MAX_POSITIONS)
    - Realized P&L above daily loss limit
    - Total P&L not already at bonus target
    """
    now_utc = datetime.utcnow()
    if not (INTRADAY_SCAN_UTC_START <= now_utc.hour < INTRADAY_SCAN_UTC_END):
        return
    if now_utc.hour >= INTRADAY_ENTRY_CUTOFF_UTC:
        return

    today = str(date.today())

    prior_scans = _load_intraday_scans(today)

    if len(prior_scans) >= INTRADAY_SCAN_MAX_RUNS:
        print(f"  📊 Intraday scan skipped: max runs ({INTRADAY_SCAN_MAX_RUNS}) reached")
        return

    if prior_scans:
        last_time = prior_scans[-1].get("scanned_at", "")
        if last_time:
            try:
                last_dt  = datetime.fromisoformat(last_time)
                mins_ago = (now_utc - last_dt).total_seconds() / 60
                if mins_ago < INTRADAY_SCAN_MIN_INTERVAL_MINS:
                    return
            except Exception:
                pass

    open_pos   = open_positions()
    open_count = len(open_pos)
    if open_count >= MAX_POSITIONS:
        print(f"  📊 Intraday scan skipped: {open_count}/{MAX_POSITIONS} slots full")
        return

    today_realized = _today_realized_pnl()
    unrealized     = sum(p.get("unrealized_pnl") or 0 for p in open_pos)
    total          = today_realized + unrealized

    if total <= DAILY_LOSS_LIMIT:
        from config.settings import TOTAL_CAPITAL as _CAP
        print(f"  ⛔ Intraday scan skipped: net P&L ${total:,.2f} ≤ loss limit ${DAILY_LOSS_LIMIT:,.0f} "
              f"(1% of ${_CAP:,}). Resumes when net P&L recovers.")
        return
    if total >= DAILY_BONUS_TARGET:
        print(f"  🏆 Intraday scan skipped: bonus target reached (${total:,.2f})")
        return

    # Re-run pool filter live — independent of premarket plan
    from scanner.pool_filter import get_pool3_tickers
    pool3_tickers = get_pool3_tickers()
    if not pool3_tickers:
        print("  📊 Intraday scan: no Pool 3 tickers available right now")
        return

    available_slots = MAX_POSITIONS - open_count
    run_num         = len(prior_scans) + 1
    print(f"\n  🔍 Intraday scan #{run_num}: {open_count}/{MAX_POSITIONS} slots | "
          f"{available_slots} available | realized ${today_realized:,.2f}")

    try:
        from scanner.intraday_momentum import scan as momentum_scan

        # Tickers already traded today — don't re-enter (open or closed)
        today_closed  = db.select("b_positions", filters={"status": "CLOSED"})
        traded_today  = (
            {p["ticker"] for p in open_pos if p.get("ticker")}
            | {p["ticker"] for p in today_closed
               if p.get("ticker") and str(p.get("opened_at", ""))[:10] == today}
        )

        candidates = [c for c in momentum_scan(pool3_tickers, broker=broker)
                      if c["ticker"] not in traded_today]
        print(f"        Momentum movers: {len(candidates)} Pool 3 stocks "
              f"up ≥{MIN_INTRADAY_MOVE_PCT:.0f}% above VWAP")

        if not candidates:
            _save_intraday_scan(today, now_utc, {"candidates": 0})
            return

        token_cap = available_slots * 3
        candidates = candidates[:token_cap]

        mkt = market_context.get()
        mkt_with_note = {
            **mkt,
            "note": (
                f"INTRADAY SCAN #{run_num}: Focus on Pool 3 momentum plays already moving today. "
                f"Use standard {TARGET_PCT*100:.0f}% targets — these will be capped at "
                f"{INTRADAY_TARGET_PCT*100:.0f}% after selection."
            ),
        }

        strategy_result = strategy.select_trades(candidates, mkt_with_note, [])
        trades = (strategy_result.get("trades") or [])[:available_slots]

        if not trades:
            _save_intraday_scan(today, now_utc, {"candidates": len(candidates), "trades": 0})
            return

        # Cap targets at 1% — less time remaining in day = smaller achievable target
        trades = _cap_intraday_targets(trades)

        approved, _rejected = risk.validate(trades)
        if not approved:
            _save_intraday_scan(today, now_utc, {"candidates": len(candidates), "rejected": len(trades)})
            return

        # ATR sizing — intraday momentum candidates don't carry atr_pct, so all pass through
        from agents import atr_sizer
        intraday_atr = {c["ticker"]: c.get("atr_pct") for c in candidates}
        approved, _ = atr_sizer.apply(approved, intraday_atr)

        final, _guard_rejected = guardrails.check(approved, broker=broker)
        if not final:
            _save_intraday_scan(today, now_utc, {"candidates": len(candidates), "guard_rejected": len(approved)})
            return

        # Save to today's existing plan
        today_plan = db.select("b_trade_plans", filters={"date": today})
        plan_id = today_plan[0]["id"] if today_plan else None
        if plan_id:
            for t in final:
                db.insert("b_planned_trades", {
                    "plan_id":          plan_id,
                    "ticker":           t["ticker"],
                    "pool":             t.get("pool", 2),
                    "action":           t["action"],
                    "entry_price":      t["entry_price"],
                    "target_price":     t["target_price"],
                    "stop_loss":        t["stop_loss"],
                    "position_size":    t["position_size"],
                    "shares":           t["shares"],
                    "estimated_profit": t.get("estimated_profit"),
                    "confidence":       t["confidence"],
                    "reasoning":        t.get("reasoning", ""),
                    "status":           "PLANNED",
                })

        run_row = db.insert("b_daily_runs", {
            "date":       today,
            "run_type":   "intraday",
            "run_number": run_num,
            "started_at": now_utc.isoformat(),
        })
        if broker == "alpaca":
            placed = place_orders(final, run_id=run_row["id"])
            print(f"  ✅ Intraday scan #{run_num}: placed {len(placed)} order(s): "
                  f"{[p['ticker'] for p in placed]}")
        else:
            placed = final
            print(f"  ✅ Intraday scan #{run_num} (simulation): "
                  f"would trade {[t['ticker'] for t in final]}")
        db.update("b_daily_runs", {"id": run_row["id"]}, {"positions_opened": len(placed)})

        _save_intraday_scan(today, now_utc, {
            "candidates": len(candidates),
            "placed":     len(placed),
        })

    except Exception as e:
        print(f"  ⚠️  Intraday scan error: {e}")


def premarket(broker: str = "alpaca") -> None:
    print(f"\n{'='*60}")
    print(f"  STRATEGY B — PREMARKET — {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    print(f"{'='*60}\n")

    if not _is_trading_day():
        print(f"[orchestrator] {date.today()} is not a NYSE trading day — skipping")
        return

    existing = db.select("b_trade_plans", filters={"date": str(date.today())})
    if existing:
        print("[orchestrator] Premarket already ran today — skipping")
        return

    if _is_halted():
        return

    # Morning sweep — close any overnight Alpaca positions before trading begins
    from agents.alpaca_broker import get_open_tickers as _get_open_tickers, _get as _alpaca_client
    overnight = _get_open_tickers()
    if overnight:
        print(f"  ⚠️  OVERNIGHT POSITIONS DETECTED: {overnight}")
        print(f"  Closing before day trading begins...\n")
        try:
            _alpaca_client().cancel_orders()
        except Exception:
            pass
        swept = close_all_positions(reason="OVERNIGHT_SWEEP")
        print(f"  Swept {len(swept)} overnight position(s). These will appear in today's Alpaca equity delta.\n")

    seed_pools_if_empty()

    # 1. Select Pool 3 for today
    print("\n[1] Selecting Pool 3 (today's elite picks)...")
    pool3_context = get_pool3_with_context()
    pool3_tickers = [m["ticker"] for m in pool3_context]
    print(f"    Pool 3: {pool3_tickers}")

    # Cap candidates to available capital
    _open_b      = db.select("b_positions", filters={"status": "OPEN"})
    _deployed_b  = sum(float(p.get("position_size") or 0) for p in _open_b)
    _available_b = TOTAL_CAPITAL - _deployed_b
    _min_size_b  = min(POSITION_SIZE_BY_CONFIDENCE.values())
    _capital_cap = max(0, int(_available_b // _min_size_b))
    if len(pool3_tickers) > _capital_cap:
        pool3_context = pool3_context[:_capital_cap]
        pool3_tickers = pool3_tickers[:_capital_cap]
        print(f"    Capital cap: trimmed to {_capital_cap} candidates "
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
    candidates = run_scan(pool3_tickers, skip_volume_surge=True)
    print(f"    {len(candidates)} candidates after scan")

    # 3.5 Earnings blackout + news intelligence
    ni_result   = news_intel.run(candidates)
    candidates  = ni_result["filtered_candidates"]
    news_context_str = ni_result["news_context"]
    if ni_result["blackout_tickers"]:
        print(f"    Blackout: {[b['ticker'] for b in ni_result['blackout_tickers']]}")
    if not candidates:
        print("[orchestrator] All candidates blocked by earnings blackout")
        return

    # 4. Claude selects trades
    print("\n[4] Claude selecting trades...")
    result = strategy.select_trades(candidates, mkt, pool3_context, news_context=news_context_str)
    trades = result.get("trades", [])

    # 5. Risk validation
    print("\n[5] Risk validation...")
    approved, risk_rejected = risk.validate(trades)

    # 5.5 Sector guard — backstop cap using SECTOR_MAP + yfinance fallback
    if approved:
        sg_result = sector_guard.run({"approved_trades": approved})
        approved  = sg_result["approved_trades"]
        if sg_result.get("sector_blocked"):
            print(f"    Sector guard blocked: {[b['ticker'] for b in sg_result['sector_blocked']]}")

    # 5.7 ATR sizing (P0) — replace formula stop with ATR-based stop + constant $150 risk
    atr_dropped: list[str] = []
    if approved:
        from agents import atr_sizer
        candidates_atr = {c["ticker"]: c.get("atr_pct") for c in candidates}
        approved, atr_dropped = atr_sizer.apply(approved, candidates_atr)
        if atr_dropped:
            print(f"    ATR sizer dropped: {atr_dropped}")
        else:
            print(f"    ATR sizer applied to {len(approved)} trade(s)")

    # 6. Guardrails
    print("\n[6] Guardrails check...")
    final, guard_rejected = guardrails.check(approved, broker=broker)

    if not final and risk_rejected:
        halt_reason = "; ".join(risk_rejected)
        db.insert("b_trade_plans", {
            "date":           str(date.today()),
            "market_context": str(mkt),
            "pool3_tickers":  pool3_tickers,
            "risk_note":      halt_reason,
            "status":         "HALTED",
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
                "plan_id":          plan_id,
                "ticker":           t["ticker"],
                "pool":             t.get("pool", 2),
                "action":           t["action"],
                "entry_price":      t["entry_price"],
                "target_price":     t["target_price"],
                "stop_loss":        t["stop_loss"],
                "position_size":    t["position_size"],
                "shares":           t["shares"],
                "estimated_profit": t.get("estimated_profit"),
                "confidence":       t["confidence"],
                "reasoning":        t.get("reasoning", ""),
                "status":           "PLANNED",
            })

    # 8. Place orders
    run_row = db.insert("b_daily_runs", {
        "date":       str(date.today()),
        "run_type":   "premarket",
        "run_number": 0,
        "started_at": datetime.utcnow().isoformat(),
    })
    if final and broker == "alpaca":
        print(f"\n[7] Placing {len(final)} orders via Alpaca...")
        placed = place_orders(final, run_id=run_row["id"])
        print(f"    Placed: {[p['ticker'] for p in placed]}")
    elif final:
        placed = final
        print(f"\n[7] Simulation mode — would trade: {[t['ticker'] for t in final]}")
    else:
        placed = []
    db.update("b_daily_runs", {"id": run_row["id"]}, {"positions_opened": len(placed)})

    print(f"\n✅ Premarket complete — {len(final)} trades | "
          f"Est. profit: ${sum(t.get('estimated_profit', 0) for t in final):.0f}")


def intraday(broker: str = "alpaca") -> None:
    print(f"\n{'='*60}")
    print(f"  STRATEGY B — INTRADAY — {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    print(f"{'='*60}\n")

    if _is_halted():
        return

    # Guard: require a successful premarket plan for today before managing positions
    today_iso = date.today().isoformat()
    if not db.select("b_trade_plans", filters={"date": today_iso}):
        print(f"  ⚠️  INTRADAY SKIPPED — no premarket plan found for {today_iso}. "
              f"Premarket must complete successfully before intraday runs.")
        return

    # 1. Manage existing positions (reconcile + trail/stop/target)
    positions = open_positions()
    if positions:
        print(f"[orchestrator] Managing {len(positions)} open positions...")
        if broker == "alpaca":
            result = update_positions_intraday()
            print(f"[orchestrator] Intraday: checked {result['checked']}, "
                  f"closed {len(result['closed'])}: {result['closed']}")
        else:
            for p in positions:
                print(f"  {p['ticker']} | entry ${p['entry_price']} | "
                      f"target ${p['target_price']} | stop ${p['stop_loss']}")
    else:
        print("[orchestrator] No open positions to manage")

    # 2. Intraday momentum scan — may open new positions in Pool 3 movers
    _maybe_run_intraday_scan(broker)


def eod(broker: str = "alpaca") -> None:
    print(f"\n{'='*60}")
    print(f"  STRATEGY B — EOD — {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    print(f"{'='*60}\n")

    if _is_halted():
        return

    # Dedup — EOD should run exactly once per day
    today_iso = date.today().isoformat()
    try:
        existing_eod = db.select("b_scan_results", filters={"date": today_iso,
                                                             "scan_type": "run_eod_started"})
        if existing_eod:
            print(f"  ⚠️  EOD already ran for {today_iso} — skipping duplicate run.")
            return
    except Exception:
        pass  # b_scan_results may not exist yet on first run; proceed

    _log_run_b("eod", "started")

    try:
        open_before = open_positions()

        print("[1] Closing all open positions...")
        if broker == "alpaca":
            closed = close_all_positions(reason="EOD")
        else:
            print("    Simulation mode — skipping close")
            closed = []

        # Alert if positions were open but nothing got closed
        if broker == "alpaca" and open_before and len(closed) == 0:
            still_open = [p["ticker"] for p in open_before]
            send_alert(
                f"[Trading Agent B] EOD close FAILED — {len(still_open)} position(s) still open",
                f"Date: {today_iso}\nStill open: {still_open}\n"
                f"These positions will carry overnight. Manual close required.",
            )

        print("\n[2] Running pool scorer...")
        try:
            scoring = score_today()
            print(f"    Scored {scoring['scored']} stocks | "
                  f"Promoted: {scoring['promoted']} | Demoted: {scoring['demoted']}")
        except Exception as e:
            print(f"  ⚠️  Pool scorer failed — tomorrow's Pool 3 will use stale scores: {e}")

        print("\n[3] Writing daily performance...")
        try:
            write_daily_performance()
        except Exception as e:
            print(f"  ⚠️  write_daily_performance failed — dashboard will show no data for today: {e}")

        print(f"\n✅ EOD complete — closed {len(closed)} position(s)")
        _log_run_b("eod", "completed", {"closed": len(closed)})

    except Exception as e:
        _log_run_b("eod", "failed", {"error": str(e)})
        send_alert(f"[Trading Agent B] EOD run FAILED — {today_iso}", f"Error: {e}")
        raise


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
