"""
Eval script for Strategy B — mirrors eval.py in Strategy A.
Usage:
  python3 eval_b.py [--days 14]        # print to console
  python3 eval_b.py [--days 14] --write  # also save result to b_scan_results

Pass criteria (same as Strategy A gate):
  - Avg daily P&L >= $500
  - Win day rate >= 80%
  - Trade win rate >= 60%
  - No integrity flags (orphaned positions, duplicate tickers, rr_violations)
  - Grade A (score >= 80)
"""
from __future__ import annotations
import argparse
from datetime import date, timedelta
from collections import defaultdict
from core import db
from config.settings import (
    DAILY_PROFIT_TARGET, TOTAL_CAPITAL,
    MIN_REWARD_RISK, MAX_POSITION_PCT, MIN_POSITION_PCT,
    DAILY_LOSS_LIMIT, DAILY_LOCK_IN_TARGET,
)

PASS_WIN_DAY_RATE   = 0.80   # 80% of trading days must be profitable
PASS_WIN_RATE       = 60.0   # 60% trade win rate
PASS_GRADE          = "A"    # Score >= 80


def _compute_metrics(perf_rows: list[dict], positions: list[dict]) -> dict:
    """
    Compute all scorecard metrics from b_daily_performance (total rows) + b_positions.
    Returns empty dict if no data.
    """
    total_rows = [r for r in perf_rows if r.get("pool") is None]
    if not total_rows:
        return {}

    days        = len(total_rows)
    total_pnl   = sum(float(r.get("gross_pnl") or 0) for r in total_rows)
    avg_pnl     = total_pnl / days
    win_days    = sum(1 for r in total_rows if float(r.get("gross_pnl") or 0) > 0)
    avg_wr_raw  = sum(float(r.get("win_rate") or 0) for r in total_rows) / days
    avg_wr      = avg_wr_raw * 100  # stored as 0–1 fraction

    closed = [p for p in positions
              if p.get("status") == "CLOSED"
              and p.get("close_reason") not in ("UNFILLED", "CLEANUP")]
    wins_t   = [p for p in closed if float(p.get("realized_pnl") or 0) > 0]
    losses_t = [p for p in closed if float(p.get("realized_pnl") or 0) <= 0]
    avg_win  = sum(float(p.get("realized_pnl") or 0) for p in wins_t)  / len(wins_t)  if wins_t  else 0
    avg_loss = sum(float(p.get("realized_pnl") or 0) for p in losses_t) / len(losses_t) if losses_t else 0
    actual_rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    # Close reasons
    close_reasons: dict[str, int] = {}
    for p in closed:
        r = p.get("close_reason") or p.get("exit_mechanism") or "UNKNOWN"
        close_reasons[r] = close_reasons.get(r, 0) + 1

    # Best / worst
    pnl_vals = [(float(p.get("realized_pnl") or 0), p.get("ticker", "?")) for p in closed]
    best_pnl,  best_ticker  = max(pnl_vals, key=lambda x: x[0])  if pnl_vals else (0, None)
    worst_pnl, worst_ticker = min(pnl_vals, key=lambda x: x[0])  if pnl_vals else (0, None)

    # Confidence cohort
    conf_stats: dict[str, dict] = {}
    for level in ("HIGH", "MEDIUM", "LOW"):
        cohort = [p for p in closed if (p.get("confidence") or "").upper() == level]
        if cohort:
            c_wins = [p for p in cohort if float(p.get("realized_pnl") or 0) > 0]
            c_pnl  = sum(float(p.get("realized_pnl") or 0) for p in cohort)
            conf_stats[level] = {
                "count":    len(cohort),
                "win_rate": len(c_wins) / len(cohort) * 100,
                "avg_pnl":  c_pnl / len(cohort),
                "total_pnl": c_pnl,
            }

    # Integrity
    eval_dates  = {str(r["date"])[:10] for r in total_rows}
    today_iso   = date.today().isoformat()
    orphaned    = [p for p in positions
                   if p.get("status") == "OPEN"
                   and str(p.get("date") or "")[:10] != today_iso]
    seen: dict[str, set] = {}
    dup_count = 0
    for p in closed:
        d = str(p.get("date") or "")[:10]
        seen.setdefault(d, set())
        if p["ticker"] in seen[d]:
            dup_count += 1
        seen[d].add(p["ticker"])

    rr_violations = [
        {"ticker": p["ticker"], "rr": round(
            (float(p["target_price"]) - float(p["entry_price"])) /
            max(float(p["entry_price"]) - float(p["stop_loss"]), 0.0001), 2
        )}
        for p in closed
        if p.get("target_price") and p.get("stop_loss") and p.get("entry_price")
        and (float(p["target_price"]) - float(p["entry_price"])) /
            max(float(p["entry_price"]) - float(p["stop_loss"]), 0.0001) < MIN_REWARD_RISK
    ]

    unfilled_count  = sum(1 for p in positions
                          if p.get("close_reason") == "UNFILLED"
                          and str(p.get("date") or "")[:10] in eval_dates)
    total_attempted = len(closed) + unfilled_count
    unfill_pct      = unfilled_count / total_attempted * 100 if total_attempted else 0

    loss_limit_days = sum(1 for r in total_rows if float(r.get("gross_pnl") or 0) < (DAILY_LOSS_LIMIT or -500))
    lock_in_days    = sum(1 for r in total_rows if float(r.get("gross_pnl") or 0) >= DAILY_LOCK_IN_TARGET)

    # Pool breakdown
    pool_rows = [r for r in perf_rows if r.get("pool") is not None]
    pool_pnl: dict[str | int, float] = {}
    for r in pool_rows:
        pool = r.get("pool")
        if str(r["date"])[:10] in eval_dates:
            pool_pnl[pool] = pool_pnl.get(pool, 0) + float(r.get("gross_pnl") or 0)

    # Grade
    pnl_score  = min(avg_pnl / DAILY_PROFIT_TARGET * 40, 40) if DAILY_PROFIT_TARGET else 0
    wd_score   = win_days / days * 30
    wr_score   = min(avg_wr / 100 * 30, 30)
    score      = pnl_score + wd_score + wr_score
    grade      = "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D"

    return dict(
        days=days, total_pnl=total_pnl, avg_daily_pnl=avg_pnl,
        win_days=win_days, win_day_rate=win_days / days,
        avg_win_rate=avg_wr, actual_rr=actual_rr,
        close_reasons=close_reasons,
        best_ticker=best_ticker, best_pnl=best_pnl,
        worst_ticker=worst_ticker, worst_pnl=worst_pnl,
        confidence_stats=conf_stats,
        orphaned=orphaned, duplicate_count=dup_count,
        rr_violations=rr_violations, unfilled_count=unfilled_count,
        total_attempted=total_attempted, unfill_pct=unfill_pct,
        loss_limit_days=loss_limit_days, lock_in_days=lock_in_days,
        pool_pnl=pool_pnl,
        score=round(score, 1), grade=grade,
    )


def _gate_check(ev: dict) -> tuple[bool, list[str], list[str]]:
    """
    Returns (passed, failures, warnings).
    passed=True only when all hard criteria are met.
    """
    failures, warnings = [], []

    if ev.get("avg_daily_pnl", 0) < DAILY_PROFIT_TARGET:
        failures.append(
            f"Avg daily P&L ${ev['avg_daily_pnl']:,.0f} < target ${DAILY_PROFIT_TARGET:,}"
        )

    if ev.get("win_day_rate", 0) < PASS_WIN_DAY_RATE:
        failures.append(
            f"Win day rate {ev['win_day_rate']*100:.0f}% < {PASS_WIN_DAY_RATE*100:.0f}% required"
        )

    if ev.get("avg_win_rate", 0) < PASS_WIN_RATE:
        failures.append(
            f"Trade win rate {ev['avg_win_rate']:.1f}% < {PASS_WIN_RATE:.0f}% required"
        )

    if ev.get("grade", "D") != PASS_GRADE:
        failures.append(f"Grade {ev.get('grade','?')} — need A (score ≥ 80), got {ev.get('score',0):.1f}")

    if ev.get("orphaned"):
        failures.append(f"{len(ev['orphaned'])} orphaned open position(s) — close manually")

    if ev.get("duplicate_count", 0) > 0:
        failures.append(f"{ev['duplicate_count']} duplicate ticker(s) same day")

    if ev.get("rr_violations"):
        failures.append(f"{len(ev['rr_violations'])} trade(s) below {MIN_REWARD_RISK}x R:R")

    if ev.get("unfill_pct", 0) >= 15:
        warnings.append(f"Unfilled rate {ev['unfill_pct']:.0f}% — entry buffer too tight")

    if ev.get("actual_rr", 0) < 2.0 and ev.get("close_reasons"):
        warnings.append(f"Actual R:R {ev['actual_rr']:.2f}x below 2.0 — review stops/targets")

    return len(failures) == 0, failures, warnings


def run_eval(days: int | None = 14, write: bool = False) -> dict:
    all_perf = db.select("b_daily_performance", order="date")
    all_pos  = db.select("b_positions")

    if days:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        all_perf = [r for r in all_perf if str(r.get("date") or "")[:10] >= cutoff]
        total_rows_in_window = [r for r in all_perf if r.get("pool") is None]
        # Filter positions to closed within the eval window
        eval_dates = {str(r["date"])[:10] for r in total_rows_in_window}
        all_pos = [p for p in all_pos
                   if str(p.get("date") or p.get("closed_at") or "")[:10] in eval_dates
                   or p.get("status") == "OPEN"]

    ev = _compute_metrics(all_perf, all_pos)
    if not ev:
        print("No performance data found.")
        return {}

    passed, failures, warnings = _gate_check(ev)

    # ── Print report ─────────────────────────────────────────────────────────
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  STRATEGY B EVAL — {ev['days']} trading day(s)")
    print(sep)

    gate_icon = "✅ PASS" if passed else "❌ FAIL"
    print(f"\n  Gate: {gate_icon}   Grade: {ev['grade']} ({ev['score']:.1f}/100)")
    print(f"\n  P&L")
    print(f"    Total:        ${ev['total_pnl']:,.2f}")
    print(f"    Avg daily:    ${ev['avg_daily_pnl']:,.2f}  (target ${DAILY_PROFIT_TARGET:,})")
    print(f"    Win days:     {ev['win_days']}/{ev['days']} ({ev['win_day_rate']*100:.0f}%)")

    print(f"\n  Trades")
    print(f"    Win rate:     {ev['avg_win_rate']:.1f}%")
    print(f"    Actual R:R:   {ev['actual_rr']:.2f}x")
    print(f"    Unfilled:     {ev['unfilled_count']}/{ev['total_attempted']} ({ev['unfill_pct']:.0f}%)")

    cr = ev.get("close_reasons", {})
    if cr:
        total_cr = sum(cr.values()) or 1
        print(f"\n  Exit reasons")
        for k, v in sorted(cr.items(), key=lambda x: -x[1]):
            print(f"    {k:<16} {v:>3}  ({v/total_cr*100:.0f}%)")

    if ev.get("best_ticker"):
        print(f"\n  Best trade:   {ev['best_ticker']} +${ev['best_pnl']:,.2f}")
    if ev.get("worst_ticker"):
        print(f"  Worst trade:  {ev['worst_ticker']} ${ev['worst_pnl']:,.2f}")

    cs = ev.get("confidence_stats", {})
    if cs:
        print(f"\n  Confidence cohort")
        for level in ("HIGH", "MEDIUM", "LOW"):
            s = cs.get(level)
            if s:
                print(f"    {level:<8}  {s['count']:>3} trades  WR {s['win_rate']:.0f}%  "
                      f"avg ${s['avg_pnl']:,.2f}  total ${s['total_pnl']:,.0f}")

    pool = ev.get("pool_pnl", {})
    if pool:
        print(f"\n  Pool P&L")
        for k, v in sorted(pool.items()):
            print(f"    Pool {k}: ${v:,.2f}")

    print(f"\n  Integrity")
    print(f"    Orphaned:     {len(ev.get('orphaned', []))}")
    print(f"    Duplicates:   {ev.get('duplicate_count', 0)}")
    print(f"    R:R violat.:  {len(ev.get('rr_violations', []))}")
    print(f"    Loss-limit days: {ev.get('loss_limit_days', 0)}/{ev['days']}")
    print(f"    Lock-in days:    {ev.get('lock_in_days', 0)}/{ev['days']}")

    if failures:
        print(f"\n  ❌ Gate failures:")
        for f in failures:
            print(f"    • {f}")
    if warnings:
        print(f"\n  ⚠️  Warnings:")
        for w in warnings:
            print(f"    • {w}")
    if passed:
        print(f"\n  ✅ All gate criteria met — Strategy B is ready for real-money evaluation.")

    print(f"\n{sep}\n")

    if write:
        try:
            db.insert("b_scan_results", {
                "date":       date.today().isoformat(),
                "scan_type":  "eval_b",
                "scanned_at": __import__("datetime").datetime.utcnow().isoformat(),
                "candidates": ev["days"],
                "placed":     0,
                "results":    {
                    "days": ev["days"], "grade": ev["grade"], "score": ev["score"],
                    "avg_daily_pnl": ev["avg_daily_pnl"], "win_day_rate": ev["win_day_rate"],
                    "avg_win_rate": ev["avg_win_rate"], "actual_rr": ev["actual_rr"],
                    "passed": passed, "failures": failures, "warnings": warnings,
                },
            })
            print("  [eval_b] Results written to b_scan_results.")
        except Exception as e:
            print(f"  [eval_b] Failed to write results: {e}")

    return {**ev, "passed": passed, "failures": failures, "warnings": warnings}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategy B eval gate")
    parser.add_argument("--days",  type=int, default=14,
                        help="Number of calendar days to evaluate (default: 14)")
    parser.add_argument("--write", action="store_true",
                        help="Save results to b_scan_results table")
    args = parser.parse_args()
    result = run_eval(days=args.days, write=args.write)
    if result and not result.get("passed"):
        raise SystemExit(1)
