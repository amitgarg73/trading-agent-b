"""
Risk Agent — validates trades before execution.
Same logic as Strategy A but writes to b_ tables and enforces blue chip limits.
"""
from __future__ import annotations
from datetime import date
from core import db
from config.settings import (
    MAX_POSITIONS, MAX_PER_SECTOR, MAX_LOSS_PER_TRADE, MIN_REWARD_RISK,
    TARGET_PCT, DAILY_LOSS_LIMIT, PRICE_SANITY_PCT, TOTAL_CAPITAL,
    MAX_POSITION_PCT, MIN_POSITION_PCT,
)
from config.blue_chips import SECTOR_MAP


def _open_positions() -> list[dict]:
    return db.select("b_positions", filters={"status": "OPEN"})


def _today_realized_pnl() -> float:
    today = str(date.today())
    rows  = db.select("b_positions", filters={"status": "CLOSED"})
    return sum(r.get("realized_pnl") or 0 for r in rows if str(r.get("closed_at", ""))[:10] == today)


def _today_net_pnl(open_pos: list[dict]) -> float:
    """Realized P&L today + unrealized on current open positions (mark-to-market)."""
    realized   = _today_realized_pnl()
    unrealized = sum(float(p.get("unrealized_pnl") or 0) for p in open_pos)
    return realized + unrealized


def validate(trades: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Returns (approved_trades, rejection_reasons).
    Filters out trades that violate risk rules.
    Loss limit uses mark-to-market P&L (realized + unrealized open positions).
    """
    open_pos  = _open_positions()
    today_pnl = _today_net_pnl(open_pos)
    approved, rejected = [], []

    if today_pnl <= DAILY_LOSS_LIMIT:
        return [], [f"Daily loss limit hit (${today_pnl:.0f} MTM) — no new trades"]

    current_tickers = {p["ticker"] for p in open_pos}
    sector_counts   = {}
    for p in open_pos:
        s = SECTOR_MAP.get(p["ticker"], "Unknown")
        sector_counts[s] = sector_counts.get(s, 0) + 1

    slots_available = MAX_POSITIONS - len(open_pos)
    if slots_available <= 0:
        return [], ["Max positions reached"]

    for trade in trades:
        ticker = trade.get("ticker", "")
        reason = None

        if ticker in current_tickers:
            reason = f"{ticker}: already have open position"

        elif len(approved) >= slots_available:
            reason = f"{ticker}: no position slots remaining"

        else:
            sector = SECTOR_MAP.get(ticker, "Unknown")
            if sector_counts.get(sector, 0) >= MAX_PER_SECTOR:
                reason = f"{ticker}: sector limit ({sector}) reached"

        if reason:
            rejected.append(reason)
            continue

        # Price sanity
        entry = trade.get("entry_price", 0)
        if entry <= 0:
            rejected.append(f"{ticker}: invalid entry price")
            continue

        # R:R check
        rr = trade.get("reward_risk", 0)
        if rr < MIN_REWARD_RISK:
            rejected.append(f"{ticker}: R:R {rr} < minimum {MIN_REWARD_RISK}")
            continue

        # Position size bounds
        ps = trade.get("position_size", 0)
        if ps > TOTAL_CAPITAL * MAX_POSITION_PCT:
            rejected.append(f"{ticker}: position size ${ps} exceeds max {MAX_POSITION_PCT*100:.0f}%")
            continue
        if ps < TOTAL_CAPITAL * MIN_POSITION_PCT:
            rejected.append(f"{ticker}: position size ${ps} below min {MIN_POSITION_PCT*100:.0f}%")
            continue

        # Update sector count for this approval
        sector = SECTOR_MAP.get(ticker, "Unknown")
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        current_tickers.add(ticker)
        approved.append(trade)

    print(f"[risk] Approved: {[t['ticker'] for t in approved]}")
    if rejected:
        print(f"[risk] Rejected: {rejected}")
    return approved, rejected
