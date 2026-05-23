"""
Pool Manager — manages Pool 1/2/3 membership in b_pools.

On first run (empty b_pools table) seeds Pool 2 with POOL_2_SEED
and Pool 1 with POOL_1_UNIVERSE.

Called by:
  - orchestrator at startup to seed if needed
  - pool_scorer (EOD) to apply daily promotions/demotions
"""
from __future__ import annotations
from datetime import date
from core import db
from config.blue_chips import POOL_2_SEED, POOL_1_UNIVERSE
from config.settings import POOL_PROMOTION_SCORE, POOL_DEMOTION_SCORE


def seed_pools_if_empty() -> None:
    """Seed b_pools with initial Pool 1 and Pool 2 stocks if table is empty."""
    existing = db.select("b_pools", limit=1)
    if existing:
        return

    rows = []
    for ticker in POOL_2_SEED:
        rows.append({"ticker": ticker, "pool": 2, "added_at": str(date.today())})
    for ticker in POOL_1_UNIVERSE:
        if ticker not in POOL_2_SEED:
            rows.append({"ticker": ticker, "pool": 1, "added_at": str(date.today())})

    for row in rows:
        try:
            db.insert("b_pools", row)
        except Exception:
            pass  # already exists

    print(f"[pool_manager] Seeded {len(POOL_2_SEED)} Pool 2 stocks, "
          f"{len(POOL_1_UNIVERSE)} Pool 1 stocks")


def get_pool(pool_number: int) -> list[str]:
    """Return list of tickers currently in the given pool."""
    rows = db.select("b_pools", filters={"pool": pool_number})
    return [r["ticker"] for r in rows]


def get_pool_with_scores(pool_number: int) -> list[dict]:
    """Return pool rows with ticker, rolling_score, trade_count, win_count."""
    return db.select("b_pools", filters={"pool": pool_number})


def apply_promotions_demotions(scored_stocks: list[dict]) -> dict:
    """
    Promote Pool 1 → Pool 2 if rolling_7d > POOL_PROMOTION_SCORE.
    Demote Pool 2 → Pool 1 if rolling_7d < POOL_DEMOTION_SCORE.
    Pool 2 seed stocks (in POOL_2_SEED) are never demoted in Phase 1.

    scored_stocks: list of {ticker, pool, rolling_7d}
    Returns: {promoted: [...], demoted: [...]}
    """
    promoted, demoted = [], []

    for s in scored_stocks:
        ticker  = s["ticker"]
        pool    = s.get("pool", 1)
        score   = s.get("rolling_7d", 0) or 0

        if pool == 1 and score >= POOL_PROMOTION_SCORE:
            db.update("b_pools", {"ticker": ticker}, {
                "pool": 2,
                "promoted_from": 1,
                "rolling_score": score,
            })
            promoted.append(ticker)

        elif pool == 2 and score <= POOL_DEMOTION_SCORE:
            db.update("b_pools", {"ticker": ticker}, {
                "pool": 1,
                "promoted_from": 2,
                "rolling_score": score,
            })
            demoted.append(ticker)

        else:
            db.update("b_pools", {"ticker": ticker}, {"rolling_score": score})

    return {"promoted": promoted, "demoted": demoted}


def update_trade_stats(ticker: str, win: bool, pnl: float) -> None:
    """Increment trade_count and win_count on the pool record."""
    rows = db.select("b_pools", filters={"ticker": ticker})
    if not rows:
        return
    row = rows[0]
    db.update("b_pools", {"ticker": ticker}, {
        "trade_count": (row.get("trade_count") or 0) + 1,
        "win_count":   (row.get("win_count") or 0) + (1 if win else 0),
    })
