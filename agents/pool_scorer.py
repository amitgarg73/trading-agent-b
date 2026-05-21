"""
Pool Scorer — runs at EOD to score every Pool 1/2 stock and update rolling scores.

Scoring factors:
  - Win/Loss (40%) — did trades on this stock win?
  - P&L magnitude (30%) — scaled to position size
  - Slippage (20%) — fill quality vs expected entry
  - Setup quality (10%) — did scanner signals align with outcome?

Rolling score: 7-day weighted average (last 2 days = 2x weight).
After scoring, applies promotions/demotions via pool_manager.
"""
from __future__ import annotations
from datetime import date, timedelta
from core import db
from core import pool_manager
import yfinance as yf
from config.settings import (
    SCORE_WEIGHT_WIN_LOSS, SCORE_WEIGHT_PNL, SCORE_WEIGHT_SLIPPAGE,
    SCORE_WEIGHT_SETUP, SCORE_ROLLING_DAYS, SCORE_RECENT_MULTIPLIER,
)


def _compute_daily_score(win: bool | None, pnl: float | None,
                          slippage_bps: float | None, setup_score: float | None) -> float:
    """Compute composite daily score 0-10 for a stock."""
    score = 0.0

    # Win/Loss component (0-10 scale before weight); 5.0 neutral if not traded
    wl = (8.0 if win else 2.0) if win is not None else 5.0
    score += wl * SCORE_WEIGHT_WIN_LOSS

    # P&L component — normalized: $200 profit = full score; 5.0 neutral if not traded
    if pnl is not None:
        pnl_score = min(10.0, max(0.0, (pnl + 100) / 30))  # -100 = 0, +200 = 10
    else:
        pnl_score = 5.0
    score += pnl_score * SCORE_WEIGHT_PNL

    # Slippage component — 0 bps = 10, 10 bps = 5, 20+ bps = 0
    if slippage_bps is not None:
        slip_score = max(0.0, 10.0 - slippage_bps)
        score += slip_score * SCORE_WEIGHT_SLIPPAGE
    else:
        score += 5.0 * SCORE_WEIGHT_SLIPPAGE  # neutral if no fill data

    # Setup quality component
    if setup_score is not None:
        score += min(10.0, setup_score) * SCORE_WEIGHT_SETUP
    else:
        score += 5.0 * SCORE_WEIGHT_SETUP  # neutral if not scanned

    return round(score, 2)


def _compute_rolling_score(ticker: str, today_score: float) -> float:
    """
    Fetch last SCORE_ROLLING_DAYS days of scores and compute weighted average.
    Recent 2 days get SCORE_RECENT_MULTIPLIER weight.
    """
    cutoff = str(date.today() - timedelta(days=SCORE_ROLLING_DAYS))
    rows = db.select("b_stock_scores", filters={"ticker": ticker})
    rows = [r for r in rows if str(r.get("date", "")) >= cutoff]
    rows.sort(key=lambda r: r["date"])

    scores  = [today_score]
    weights = [SCORE_RECENT_MULTIPLIER]

    for r in rows[-SCORE_ROLLING_DAYS:]:
        s = r.get("daily_score")
        if s is not None:
            d_str  = str(r["date"])
            age    = (date.today() - date.fromisoformat(d_str)).days
            weight = SCORE_RECENT_MULTIPLIER if age <= 2 else 1.0
            scores.append(float(s))
            weights.append(weight)

    if not scores:
        return today_score

    return round(sum(s * w for s, w in zip(scores, weights)) / sum(weights), 2)


def score_today() -> dict:
    """
    Main EOD scoring run.
    1. Pull today's closed positions from b_positions
    2. Score each traded stock
    3. Score untraded Pool 2 stocks (setup quality only)
    4. Write to b_stock_scores
    5. Apply pool promotions/demotions
    """
    today   = str(date.today())
    scored  = []

    # --- Scored traded stocks ---
    closed = db.select("b_positions", filters={"status": "CLOSED"})
    today_closed = [p for p in closed if str(p.get("closed_at", ""))[:10] == today]

    traded_tickers = set()
    for pos in today_closed:
        ticker   = pos["ticker"]
        pool     = pos.get("pool", 2)
        entry    = float(pos.get("entry_price") or 0)
        fill     = float(pos.get("fill_price") or 0)
        close_px = float(pos.get("close_price") or 0)
        pnl      = float(pos.get("realized_pnl") or 0)
        win      = pnl > 0

        # Slippage: planned entry vs actual Alpaca fill price
        if fill and entry:
            slippage_bps = round(abs(fill - entry) / entry * 10_000, 1)
        else:
            slippage_bps = 0.0

        daily = _compute_daily_score(win, pnl, slippage_bps, None)
        rolling = _compute_rolling_score(ticker, daily)

        row = {
            "date":        today,
            "ticker":      ticker,
            "pool":        pool,
            "traded":      True,
            "win":         win,
            "pnl":         pnl,
            "slippage_bps": slippage_bps,
            "setup_score": None,
            "daily_score": daily,
            "rolling_7d":  rolling,
        }
        db.upsert("b_stock_scores", row, on_conflict="date,ticker")
        pool_manager.update_trade_stats(ticker, win, pnl)
        scored.append({"ticker": ticker, "pool": pool, "rolling_7d": rolling})
        traded_tickers.add(ticker)

    # --- Score untraded Pool 2 stocks (setup quality = neutral) ---
    pool2 = pool_manager.get_pool(2)
    for ticker in pool2:
        if ticker in traded_tickers:
            continue
        daily   = _compute_daily_score(None, None, None, 5.0)  # neutral
        rolling = _compute_rolling_score(ticker, daily)
        row = {
            "date":        today,
            "ticker":      ticker,
            "pool":        2,
            "traded":      False,
            "win":         None,
            "pnl":         None,
            "slippage_bps": None,
            "setup_score": 5.0,
            "daily_score": daily,
            "rolling_7d":  rolling,
        }
        db.upsert("b_stock_scores", row, on_conflict="date,ticker")
        scored.append({"ticker": ticker, "pool": 2, "rolling_7d": rolling})

    # --- Apply promotions/demotions ---
    changes = pool_manager.apply_promotions_demotions(scored)

    print(f"[pool_scorer] Scored {len(scored)} stocks | "
          f"Promoted: {changes['promoted']} | Demoted: {changes['demoted']}")

    return {
        "scored":   len(scored),
        "traded":   len(traded_tickers),
        "promoted": changes["promoted"],
        "demoted":  changes["demoted"],
    }


def _print_unfilled_analysis(today_closed: list) -> None:
    """For UNFILLED positions, check if target would have been hit intraday."""
    unfilled = [p for p in today_closed if p.get("close_reason") == "UNFILLED"]
    if not unfilled:
        return

    seen, unique = set(), []
    for p in unfilled:
        if p["ticker"] not in seen:
            seen.add(p["ticker"])
            unique.append(p)

    print(f"\n  📋 UNFILLED order analysis ({len(unique)} ticker(s)):")
    for p in unique:
        ticker = p["ticker"]
        target = p.get("target_price")
        stop   = p.get("stop_loss")
        entry  = p.get("entry_price")
        try:
            data = yf.Ticker(ticker).history(period="1d", interval="5m")
            if data.empty or target is None:
                print(f"     {ticker:6s}  no intraday data available")
                continue
            intraday_high    = round(float(data["High"].max()), 2)
            intraday_low     = round(float(data["Low"].min()), 2)
            would_hit_target = intraday_high >= target
            would_hit_stop   = intraday_low  <= stop if stop else False
            outcome = "✅ TARGET would hit" if would_hit_target else ("🔴 STOP would hit" if would_hit_stop else "➖ Neither hit")
            print(f"     {ticker:6s}  entry ${entry}  target ${target}  day high ${intraday_high}  →  {outcome}")
        except Exception as e:
            print(f"     {ticker:6s}  analysis failed: {e}")


def write_daily_performance() -> None:
    """Compute and store daily P&L summary by pool in b_daily_performance.
    Also logs today's regime for passive analysis."""
    from agents.market_context import get as get_market_context, get_regime_label

    today   = str(date.today())
    closed  = db.select("b_positions", filters={"status": "CLOSED"})
    today_c = [p for p in closed if str(p.get("closed_at", ""))[:10] == today]

    # Fetch regime data once
    mkt          = get_market_context()
    vix          = mkt.get("vix_level")
    fear_greed   = mkt.get("fear_greed")
    spy_change   = mkt.get("spy_change_pct")
    regime_label = get_regime_label(vix, fear_greed, spy_change)
    print(f"[pool_scorer] Regime today: {regime_label} "
          f"(VIX={vix}, F&G={fear_greed}, SPY={spy_change}%)")

    by_pool: dict[int | None, list] = {}
    for p in today_c:
        pool = p.get("pool")
        by_pool.setdefault(pool, []).append(p)
    by_pool.setdefault(None, today_c)  # total across all pools

    for pool, positions in by_pool.items():
        wins     = [p for p in positions if (p.get("realized_pnl") or 0) > 0]
        gross    = sum(p.get("realized_pnl") or 0 for p in positions)
        n        = len(positions)
        win_rate = round(len(wins) / n, 4) if n else 0
        avg_pnl  = round(gross / n, 2) if n else 0
        avg_win  = sum(p.get("realized_pnl") or 0 for p in wins) / len(wins) if wins else 0
        losses   = [p for p in positions if (p.get("realized_pnl") or 0) <= 0]
        avg_loss = sum(p.get("realized_pnl") or 0 for p in losses) / len(losses) if losses else 0
        exp      = round(win_rate * avg_win + (1 - win_rate) * avg_loss, 2)

        # Alpaca equity reconciliation — only for the total row (pool=None)
        alpaca_equity = None
        friction_gap  = None
        if pool is None:
            try:
                from agents.alpaca_broker import _get as _broker
                account       = _broker().get_account()
                alpaca_equity = round(float(account.equity), 2)
                # Strategy B cumulative P&L (all closed positions ever)
                all_b_perf    = db.select("b_daily_performance", filters={"pool": None})
                cumulative_b  = sum(r.get("gross_pnl", 0) or 0 for r in all_b_perf)
                our_calc      = round(50_000 + cumulative_b + round(gross, 2), 2)
                friction_gap  = round(alpaca_equity - our_calc, 2)
                gap_sign      = "+" if friction_gap >= 0 else ""
                print(f"[pool_scorer] Alpaca equity=${alpaca_equity:,.2f} | "
                      f"B calc=${our_calc:,.2f} | gap={gap_sign}${friction_gap:,.2f}")
            except Exception as e:
                print(f"[pool_scorer] Alpaca equity fetch failed: {e}")

        row = {
            "date":              today,
            "pool":              pool,
            "trades_taken":      n,
            "wins":              len(wins),
            "losses":            len(losses),
            "gross_pnl":         round(gross, 2),
            "win_rate":          win_rate,
            "avg_pnl_per_trade": avg_pnl,
            "expectancy":        exp,
            "vix_level":         vix,
            "fear_greed":        fear_greed,
            "spy_change_pct":    spy_change,
            "regime_label":      regime_label,
            "alpaca_equity":     alpaca_equity,
            "friction_gap":      friction_gap,
        }
        db.upsert("b_daily_performance", row, on_conflict="date,pool")

    _print_unfilled_analysis(today_c)
