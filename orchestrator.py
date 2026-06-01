"""
Orchestrator — Strategy B entry point.
Called by GitHub Actions: python orchestrator.py --mode premarket|intraday|eod
"""
from __future__ import annotations
import argparse
import os
from datetime import date, datetime

from scanner.scanner import run_scan
from scanner.pool_filter import get_pool3_tickers, get_pool3_with_context
from agents import strategy, risk, guardrails, market_context, news_intel, sector_guard
from agents.alpaca_broker import place_orders, update_positions_intraday, close_all_positions, open_positions
from agents.pool_scorer import score_today, write_daily_performance
from core import db, ledger
from core.alerts import send_alert
from core.pool_manager import seed_pools_if_empty


def _write_summary(md: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a") as f:
                f.write(md + "\n")
        except Exception:
            pass


def _premarket_summary(
    run_time: str,
    pool3_tickers: list,
    mkt: dict,
    spy_pct: float | None,
    n_after_scan: int,
    n_after_filters: int,
    n_sent_to_claude: int,
    candidates_sent: list,
    trades_selected: list,
    risk_rejected: list,
    guard_rejected: list,
    final: list,
    claude_reasoning: str,
) -> str:
    fg     = mkt.get("fear_greed", "?")
    fg_lbl = mkt.get("fear_greed_label", "")
    vix    = mkt.get("vix_level", "?")
    bias   = mkt.get("futures_bias", "?")
    spy_m  = mkt.get("spy_change_pct", spy_pct or 0)
    sectors = mkt.get("sector_rotation", {})
    leaders  = [(k, v) for k, v in sorted(sectors.items(), key=lambda x: -x[1]) if v > 0][:3]
    laggards = [(k, v) for k, v in sorted(sectors.items(), key=lambda x: x[1])  if v < 0][:3]

    lines = [
        f"## Strategy B Premarket — {run_time}",
        "",
        "### Market",
        f"| Signal | Value |",
        f"|--------|-------|",
        f"| SPY | {spy_m:+.2f}% |",
        f"| VIX | {vix} |",
        f"| Fear & Greed | {fg} — {fg_lbl} |",
        f"| Bias | {bias} |",
    ]
    if leaders:
        lines.append(f"| Sector leaders | {', '.join(f'{k} {v:+.1f}%' for k,v in leaders)} |")
    if laggards:
        lines.append(f"| Sector laggards | {', '.join(f'{k} {v:+.1f}%' for k,v in laggards)} |")

    lines += [
        "",
        "### Candidate Pipeline",
        f"| Stage | Count |",
        f"|-------|-------|",
        f"| Pool 3 | {len(pool3_tickers)} |",
        f"| After scan | {n_after_scan} |",
        f"| After filters (VWAP/ORB/range) | {n_after_filters} |",
        f"| Sent to Claude | {n_sent_to_claude} |",
        f"| Claude selected | {len(trades_selected)} |",
        f"| After risk + guardrails | **{len(final)}** |",
    ]

    if candidates_sent:
        lines += ["", "### Candidates Claude Evaluated"]
        lines.append("| Ticker | Score | Vol Ratio | Above VWAP | SPY RS | Today % |")
        lines.append("|--------|-------|-----------|------------|--------|---------|")
        for c in candidates_sent:
            vwap_icon = "✅" if c.get("above_vwap") else "❌"
            lines.append(
                f"| {c.get('ticker','')} "
                f"| {c.get('technical_score', c.get('score','?'))} "
                f"| {c.get('volume_ratio', c.get('vol_ratio','?'))} "
                f"| {vwap_icon} "
                f"| {c.get('rs_vs_spy','?')} "
                f"| {c.get('today_pct_change','?')} |"
            )

    all_rejected = risk_rejected + guard_rejected
    if all_rejected:
        lines += ["", f"### Rejected", f"Risk/guardrails: {', '.join(all_rejected)}"]

    if claude_reasoning:
        lines += ["", "### Claude's Reasoning", f"> {claude_reasoning[:600].replace(chr(10), ' ')}"]

    outcome = f"**{len(final)} trade(s) placed**" if final else "**No trades today**"
    lines += ["", f"### Outcome: {outcome}"]
    if final:
        for t in final:
            lines.append(f"- {t['ticker']}: entry ${t.get('entry_price','?')} | "
                         f"target ${t.get('target_price','?')} | "
                         f"stop ${t.get('stop_loss','?')} | "
                         f"size ${t.get('position_size','?')}")

    return "\n".join(lines)


def _sweep_and_verify() -> bool:
    """
    Close overnight Alpaca positions with one retry and a verification step.
    Returns True if Alpaca is clear after either attempt.
    Returns False if positions remain after both — halt flag is set and alert sent.

    Only acts on positions tracked in our DB as OPEN — skips Strategy A's positions
    on the shared Alpaca account.
    """
    import time
    from agents.alpaca_broker import get_open_tickers as _get_open_tickers, _get as _alpaca_client

    overnight = _get_open_tickers()
    if not overnight:
        return True

    # Cross-strategy guard: only act on positions we opened (in our DB as OPEN)
    our_open = {p["ticker"] for p in open_positions()}
    ours_overnight = overnight & our_open
    if not ours_overnight:
        return True

    print(f"  ⚠️  OVERNIGHT POSITIONS DETECTED: {ours_overnight}")
    print("  Closing before day trading begins...")
    try:
        _alpaca_client().cancel_orders()
    except Exception:
        pass
    close_all_positions(reason="OVERNIGHT_SWEEP")

    time.sleep(10)
    remaining = _get_open_tickers() & our_open
    if not remaining:
        print("  ✅ Morning sweep complete — Alpaca is clear.\n")
        return True

    print(f"  ⚠️  Positions still open after first sweep: {remaining} — retrying...")
    close_all_positions(reason="OVERNIGHT_SWEEP")
    time.sleep(10)
    remaining = _get_open_tickers() & our_open
    if not remaining:
        print("  ✅ Cleared on second attempt.\n")
        return True

    tickers = sorted(remaining)
    ledger.log("sweep_failed", {"tickers": tickers})
    db.insert("b_scan_results", {
        "date":      str(date.today()),
        "scan_type": "halt_flag",
        "results": {
            "reason":     f"Morning sweep failed — positions still open: {tickers}",
            "halted_at":  datetime.utcnow().isoformat(),
            "halted_by":  "sweep_and_verify",
            "positions_closed": [],
        },
    })
    send_alert(
        "STRATEGY B HALTED — Morning Sweep Failed",
        f"Positions still open after 2 close attempts: {', '.join(tickers)}\n\n"
        f"STEP 1 — Close positions manually in Alpaca:\n"
        f"  https://app.alpaca.markets/paper/dashboard/overview\n"
        f"  Find these tickers and close each one: {', '.join(tickers)}\n\n"
        f"STEP 2 — Restart Strategy B:\n"
        f"  https://github.com/amitgarg73/trading-agent-b/actions/workflows/restart.yml\n"
        f"  Click 'Run workflow' then confirm.\n\n"
        f"No new trades will open until you complete both steps.",
    )
    print(f"  ❌ Sweep failed after 2 attempts — premarket halted. Alert sent.")
    return False


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
    MAX_POSITIONS, MAX_DAILY_ENTRIES, DAILY_BONUS_TARGET, DAILY_LOSS_LIMIT,
    INTRADAY_SCAN_UTC_START, INTRADAY_SCAN_UTC_END, INTRADAY_ENTRY_CUTOFF_UTC,
    INTRADAY_SCAN_MAX_RUNS, INTRADAY_SCAN_MIN_INTERVAL_MINS,
    MIN_INTRADAY_MOVE_PCT, MIN_SPY_MOVE_PCT,
    TARGET_PCT, MAX_LOSS_PER_TRADE, TRAIL_PCT,
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
    halt_rows = db.select("b_scan_results", filters={"scan_type": "halt_flag"})
    if halt_rows:
        print("🛑 Strategy B halted — halt flag set")
        return True
    rows = db.select("b_trade_plans", filters={"status": "HALTED"})
    if rows and rows[0].get("date") == str(date.today()):
        print("🛑 Strategy B halted for today")
        return True
    return False


def _today_realized_pnl() -> float:
    today = str(date.today())
    rows = db.select("b_positions", filters={"status": "CLOSED"},
                     filters_gte={"closed_at": f"{today}T00:00:00"})
    return sum(
        r.get("realized_pnl") or 0
        for r in rows
        if r.get("close_reason") not in ("CLEANUP", "UNFILLED")
    )



def _fetch_atr_for_tickers(tickers: list[str]) -> dict[str, float | None]:
    """
    Batch-fetch 14-day ATR% for a small set of tickers via Alpaca daily bars.
    Returns {ticker: atr_pct} — None for any ticker that fails.
    Used by intraday ATR sizer so stops survive normal intraday noise.
    """
    from datetime import datetime, timedelta
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from agents.alpaca_broker import _dclient as get_data_client

    if not tickers:
        return {}

    result: dict[str, float | None] = {t: None for t in tickers}
    end   = datetime.utcnow()
    start = end - timedelta(days=28)
    try:
        req  = StockBarsRequest(symbol_or_symbols=tickers, timeframe=TimeFrame.Day,
                                start=start, end=end)
        bars = get_data_client().get_stock_bars(req).data
        for ticker in tickers:
            try:
                ticker_bars = bars.get(ticker) or []
                if len(ticker_bars) < 10:
                    continue
                records = [{"h": b.high, "l": b.low, "c": b.close} for b in ticker_bars[-20:]]
                trs = []
                for i in range(1, len(records)):
                    h, l, prev_c = records[i]["h"], records[i]["l"], records[i - 1]["c"]
                    trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
                atr   = sum(trs[-14:]) / min(14, len(trs))
                price = records[-1]["c"]
                if price > 0:
                    result[ticker] = round(atr / price * 100, 2)
            except Exception:
                pass
    except Exception as e:
        print(f"  [atr_fetch] Alpaca batch failed: {e}")

    for t, v in result.items():
        if v is None:
            print(f"  [atr_fetch] {t}: no ATR — formula stop will apply")
    return result


def _get_gap_up_tickers(min_gap_pct: float = 2.0, top_n: int = 20) -> list[dict]:
    """
    Fetch today's top gap-up movers via Alpaca ScreenerClient.
    Returns [{"ticker": str, "gap_pct": float, "price": float}].
    Silently returns [] on any failure.
    """
    try:
        import os
        from alpaca.data.historical.screener import ScreenerClient
        from alpaca.data.requests import MarketMoversRequest
        from alpaca.data.enums import MarketType
        client = ScreenerClient(
            os.environ.get("ALPACA_API_KEY", ""),
            os.environ.get("ALPACA_SECRET_KEY", ""),
        )
        movers = client.get_market_movers(MarketMoversRequest(market_type=MarketType.STOCKS, top=top_n))
        return [
            {"ticker": m.symbol, "gap_pct": m.percent_change, "price": m.price}
            for m in (movers.gainers or [])
            if m.percent_change >= min_gap_pct
        ]
    except Exception as e:
        print(f"  [gap_up] ScreenerClient failed: {e}")
        return []


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

    # Daily entry cap — prevents over-trading on high-vol days where stops free slots quickly
    all_pos_today = db.select("b_positions", filters_gte={"opened_at": f"{today}T00:00:00"})
    daily_opened  = len(all_pos_today)
    if daily_opened >= MAX_DAILY_ENTRIES:
        print(f"  📊 Intraday scan skipped: daily entry cap hit ({daily_opened}/{MAX_DAILY_ENTRIES})")
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

    # SPY gate — require SPY up ≥MIN_SPY_MOVE_PCT% for intraday entries.
    # Prevents opening positions on flat/down market days where momentum setups fail.
    if broker == "alpaca":
        try:
            from agents.alpaca_broker import get_intraday_signals
            _spy_pct = get_intraday_signals(["SPY"]).get("SPY", {}).get("today_pct_change", 0)
            _threshold = MIN_SPY_MOVE_PCT * 100
            if _spy_pct < _threshold:
                print(f"  ⛔ Intraday scan skipped: SPY {_spy_pct:+.2f}% < {_threshold:.1f}% gate")
                _save_intraday_scan(today, now_utc, {"candidates": 0, "reason": f"SPY gate {_spy_pct:+.2f}%"})
                return
            print(f"  ✅ SPY gate: {_spy_pct:+.2f}% ≥ {_threshold:.1f}% — intraday scan allowed")
        except Exception as _e:
            print(f"  ⚠️  SPY gate check failed: {_e} — proceeding anyway")

    # Re-run pool filter live — independent of premarket plan
    from scanner.pool_filter import get_pool3_tickers
    pool3_tickers = get_pool3_tickers()
    if not pool3_tickers:
        print("  📊 Intraday scan: no Pool 3 tickers available right now")
        return

    available_slots = min(MAX_POSITIONS - open_count, MAX_DAILY_ENTRIES - daily_opened)
    run_num         = len(prior_scans) + 1
    print(f"\n  🔍 Intraday scan #{run_num}: {open_count}/{MAX_POSITIONS} slots | "
          f"{available_slots} available | realized ${today_realized:,.2f}")

    try:
        from scanner.intraday_momentum import scan as momentum_scan

        # Tickers already traded today — don't re-enter (open or closed)
        today_closed  = db.select("b_positions", filters={"status": "CLOSED"},
                                  filters_gte={"opened_at": f"{today}T00:00:00"})
        traded_today  = (
            {p["ticker"] for p in open_pos if p.get("ticker")}
            | {p["ticker"] for p in today_closed if p.get("ticker")}
        )

        candidates = [c for c in momentum_scan(pool3_tickers, broker=broker)
                      if c["ticker"] not in traded_today]
        print(f"        Momentum movers: {len(candidates)} Pool 3 stocks "
              f"up ≥{MIN_INTRADAY_MOVE_PCT:.0f}% above VWAP")

        # Gap-up injection for intraday scan
        intraday_tickers = {c["ticker"] for c in candidates}
        gap_ups = _get_gap_up_tickers()
        for g in gap_ups:
            if g["ticker"] not in traded_today and g["ticker"] not in intraday_tickers:
                candidates.append({
                    "ticker":          g["ticker"],
                    "technical_score": 6,
                    "current_price":   g["price"],
                    "signals":         ["gap_up"],
                    "_source":         "gap_up",
                    "premarket_change_pct": g["gap_pct"],
                })
                intraday_tickers.add(g["ticker"])
        if gap_ups:
            print(f"        Gap-up injection: {len(candidates)} total candidates after movers")

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
                f"Use standard {TARGET_PCT*100:.0f}% targets. "
                f"The trailing stop ({TRAIL_PCT*100:.0f}%) is the real exit; the target is a safety ceiling."
            ),
        }

        strategy_result = strategy.select_trades(candidates, mkt_with_note, [])
        trades = (strategy_result.get("trades") or [])[:available_slots]

        if not trades:
            _save_intraday_scan(today, now_utc, {"candidates": len(candidates), "trades": 0})
            return

        approved, _rejected = risk.validate(trades)
        if not approved:
            _save_intraday_scan(today, now_utc, {"candidates": len(candidates), "rejected": len(trades)})
            return

        # ATR sizing — fetch real 14-day ATR for intraday candidates so stops are
        # sized to survive normal intraday noise (formula stop of 0.67% is too tight
        # for high-ATR names like NVDA which move $4-5/day).
        from agents import atr_sizer
        intraday_atr = _fetch_atr_for_tickers([t["ticker"] for t in approved])
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

    if _is_halted():
        return

    existing = db.select("b_trade_plans", filters={"date": str(date.today())})
    if existing:
        print("[orchestrator] Premarket already ran today — skipping")
        return

    # Pipeline counters for summary
    _n_after_scan = _n_after_filters = 0
    _dropped = _dropped_orb = _dropped_top = 0
    _spy_pct: float | None = None
    _max_positions_today = MAX_POSITIONS
    _candidates_sent: list = []
    _trades_selected: list = []
    _risk_rejected: list = []
    _guard_rejected: list = []
    _final: list = []
    _claude_reasoning = ""
    _mkt: dict = {}

    # Morning sweep — close any overnight Alpaca positions before trading begins
    if not _sweep_and_verify():
        return

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
    _mkt = mkt

    # 3. Scan Pool 3 tickers
    print(f"\n[3] Scanning {len(pool3_tickers)} Pool 3 tickers...")
    scanner_results = run_scan(pool3_tickers, skip_volume_surge=True)
    scanned_tickers = {c["ticker"] for c in scanner_results}
    print(f"    {len(scanner_results)} scanner candidates")
    _n_after_scan = len(scanner_results)

    # Merge scanner hits with pool_filter context for tickers the scanner missed.
    # Pool_filter already scored and curated these tickers with live Alpaca data;
    # the scanner may drop them due to yfinance rate limits or the ATR gate.
    # Scanner results win (they carry richer technical detail); pool_filter fills gaps.
    pool3_missed = [
        {**c, "technical_score": 0, "signals": ["pool3_context"],
         "current_price": c.get("cur_price")}  # normalize field name for live price update
        for c in pool3_context
        if c["ticker"] not in scanned_tickers
    ]
    candidates = scanner_results + pool3_missed
    if pool3_missed:
        print(f"    Pool3 fill-in: {len(pool3_missed)} tickers scanner missed → {len(candidates)} total")
    else:
        print(f"    Scanner covered all pool3 tickers → {len(candidates)} candidates")

    # 3.1 Gap-up injection — Alpaca market movers (≥2%) not already in candidate list
    existing_tickers = {c["ticker"] for c in candidates}
    gap_ups = _get_gap_up_tickers()
    gap_injected = []
    for g in gap_ups:
        if g["ticker"] not in existing_tickers:
            gap_injected.append({
                "ticker":          g["ticker"],
                "technical_score": 6,
                "current_price":   g["price"],
                "signals":         ["gap_up"],
                "_source":         "gap_up",
                "premarket_change_pct": g["gap_pct"],
            })
            existing_tickers.add(g["ticker"])
    if gap_injected:
        candidates = candidates + gap_injected
        print(f"    Gap-up injection: {len(gap_injected)} new mover(s) added → {len(candidates)} total")

    # 3.2 Live price refresh + VWAP enrichment via Alpaca snapshot (batch call)
    if broker == "alpaca":
        from concurrent.futures import ThreadPoolExecutor
        from agents.alpaca_broker import get_live_prices, get_intraday_signals
        tickers         = [c["ticker"] for c in candidates]
        signal_tickers  = list(set(tickers + ["SPY"]))
        with ThreadPoolExecutor(max_workers=2) as executor:
            f_prices  = executor.submit(get_live_prices, tickers)
            f_signals = executor.submit(get_intraday_signals, signal_tickers)
            live          = f_prices.result()
            intraday_sigs = f_signals.result()
        updated = 0
        for c in candidates:
            ask = live.get(c["ticker"])
            cur = c.get("current_price") or c.get("price") or 0
            if ask and cur and abs(ask - cur) / cur < 0.10:
                c["current_price"] = ask
                updated += 1
        enriched = 0
        for c in candidates:
            sig = intraday_sigs.get(c["ticker"])
            if sig:
                c.update(sig)
                enriched += 1
        candidates.sort(key=lambda x: (not x.get("above_vwap", False), -(x.get("rs_vs_spy") or 0)))
        above_vwap = sum(1 for c in candidates if c.get("above_vwap"))
        print(f"    Live price: {updated}/{len(candidates)} updated | VWAP: {enriched}/{len(candidates)} enriched — {above_vwap} above VWAP")

        # Drop stocks already extended from open on weak volume (chasing exhausted momentum).
        pre_ext = len(candidates)
        candidates = [
            c for c in candidates
            if not (
                (c.get("today_pct_change") or 0) > 3.0
                and (c.get("volume_ratio") or 0) < 0.7
            )
        ]
        dropped = pre_ext - len(candidates)
        if dropped:
            print(f"    Extension filter: dropped {dropped} extended-low-vol candidate(s)")

        # Drop stocks still inside the opening range — price below ORB high = no breakout.
        pre_orb = len(candidates)
        candidates = [c for c in candidates if c.get("above_orb") is not False]
        dropped_orb = pre_orb - len(candidates)
        if dropped_orb:
            print(f"    ORB filter: dropped {dropped_orb} inside-range candidate(s)")

        # Drop stocks in the top 15% of today's day range — near-day-high entries have little
        # upside and high retracement risk.
        pre_top = len(candidates)
        candidates = [
            c for c in candidates
            if not (
                c.get("day_high") and c.get("day_low")
                and (c["day_high"] - c["day_low"]) > 0
                and ((c.get("current_price") or c.get("price") or 0) - c["day_low"]) /
                    (c["day_high"] - c["day_low"]) > 0.85
            )
        ]
        dropped_top = pre_top - len(candidates)
        if dropped_top:
            print(f"    Top-of-range filter: dropped {dropped_top} near-day-high candidate(s)")

        # SPY premarket gate — if SPY opened negative, reduce slots instead of skipping entirely.
        _n_after_filters = len(candidates)
        _spy_pct = intraday_sigs.get("SPY", {}).get("today_pct_change", None)
        if _spy_pct is not None and _spy_pct < 0:
            _max_positions_today = max(0, MAX_POSITIONS - 3)
            print(f"    ⚠️  SPY premarket: {_spy_pct:+.2f}% — market opened negative. "
                  f"Reducing max positions {MAX_POSITIONS}→{_max_positions_today}.")
        elif _spy_pct is not None:
            print(f"    SPY premarket: {_spy_pct:+.2f}% ✅")

    # 3.4 Persist premarket scan candidates for observability
    _premarket_candidate_fields = [
        "ticker", "technical_score", "current_price", "above_vwap", "vwap",
        "rs_vs_spy", "today_pct_change", "volume_ratio", "atr_pct",
        "above_orb", "signals", "sector", "pool",
    ]
    _premarket_candidates_slim = [
        {k: c.get(k) for k in _premarket_candidate_fields} for c in candidates
    ]
    try:
        db.insert("b_scan_results", {
            "date":       str(date.today()),
            "scan_type":  "premarket",
            "scanned_at": datetime.utcnow().isoformat(),
            "candidates": len(_premarket_candidates_slim),
            "placed":     0,
            "results": {
                "pool3_count":  len(pool3_tickers),
                "after_scan":   len(candidates),
                "candidate_list": _premarket_candidates_slim,
            },
        })
    except Exception as _e:
        print(f"    [warn] Failed to save premarket scan row: {_e}")

    # 3.5 Earnings blackout + news intelligence
    ni_result   = news_intel.run(candidates)
    candidates  = ni_result["filtered_candidates"]
    news_context_str = ni_result["news_context"]
    if ni_result["blackout_tickers"]:
        print(f"    Blackout: {[b['ticker'] for b in ni_result['blackout_tickers']]}")
    if not candidates:
        print("[orchestrator] No candidates to trade")
        return

    # 3.8 Trade cap — top min(MAX_DAILY_ENTRIES, _max_positions_today) by score before Claude sees them.
    _entry_cap = min(MAX_DAILY_ENTRIES, _max_positions_today)
    if len(candidates) > _entry_cap:
        candidates.sort(key=lambda x: x.get("technical_score") or 0, reverse=True)
        candidates = candidates[:_entry_cap]
        print(f"    Trade cap: top {_entry_cap} candidates by score sent to Claude")

    _candidates_sent = candidates

    # 4. Claude selects trades
    print("\n[4] Claude selecting trades...")
    result = strategy.select_trades(candidates, mkt, pool3_context, news_context=news_context_str)
    trades = result.get("trades", [])
    _trades_selected  = trades
    _claude_reasoning = result.get("reasoning", "")

    # 5. Risk validation
    print("\n[5] Risk validation...")
    approved, risk_rejected = risk.validate(trades)
    _risk_rejected = risk_rejected

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
    _guard_rejected = guard_rejected
    _final = final

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

    # 7. Save plan — always write so intraday guard doesn't block on 0-trade premarket
    plan_id = None
    plan_row = db.insert("b_trade_plans", {
        "date":                   str(date.today()),
        "market_context":         str(mkt),
        "pool3_tickers":          pool3_tickers,
        "total_estimated_profit": sum(t.get("estimated_profit", 0) for t in final),
        "risk_note":              f"Rejected: {risk_rejected + guard_rejected}",
        "status":                 "ACTIVE" if (final or trades) else "NO_TRADES",
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

    _write_summary(_premarket_summary(
        run_time         = datetime.now().strftime("%Y-%m-%d %H:%M ET"),
        pool3_tickers    = pool3_tickers,
        mkt              = mkt,
        spy_pct          = _spy_pct,
        n_after_scan     = _n_after_scan,
        n_after_filters  = _n_after_filters,
        n_sent_to_claude = len(_candidates_sent),
        candidates_sent  = _candidates_sent,
        trades_selected  = _trades_selected,
        risk_rejected    = _risk_rejected,
        guard_rejected   = _guard_rejected,
        final            = _final,
        claude_reasoning = _claude_reasoning,
    ))


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


def entry_scan(broker: str = "alpaca") -> None:
    print(f"\n{'='*60}")
    print(f"  STRATEGY B — ENTRY SCAN — {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    print(f"{'='*60}\n")

    if _is_halted():
        return

    today_iso = date.today().isoformat()
    if not db.select("b_trade_plans", filters={"date": today_iso}):
        print(f"  ⚠️  ENTRY SCAN SKIPPED — no premarket plan found for {today_iso}.")
        return

    _maybe_run_intraday_scan(broker)


def eod(broker: str = "alpaca") -> None:
    print(f"\n{'='*60}")
    print(f"  STRATEGY B — EOD — {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    print(f"{'='*60}\n")

    if not _is_trading_day():
        print(f"[orchestrator] {date.today()} is not a NYSE trading day — skipping EOD")
        return
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

        # Alert if any position that was open before EOD is still open after close
        if broker == "alpaca" and open_before:
            open_before_ids = {p["id"] for p in open_before}
            open_after = db.select("b_positions", filters={"status": "OPEN"})
            still_open = [p["ticker"] for p in open_after if p["id"] in open_before_ids]
            if still_open:
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
    parser.add_argument("--mode",   required=True, choices=["premarket", "intraday", "eod", "entry_scan"])
    parser.add_argument("--broker", default="alpaca", choices=["alpaca", "simulation"])
    args = parser.parse_args()

    if args.mode == "premarket":
        premarket(args.broker)
    elif args.mode == "intraday":
        intraday(args.broker)
    elif args.mode == "entry_scan":
        entry_scan(args.broker)
    elif args.mode == "eod":
        eod(args.broker)
