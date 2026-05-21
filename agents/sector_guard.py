"""
Sector Guard — prevents over-concentration in one sector.
Runs after risk agent, before guardrails — no Claude API call needed.

Uses SECTOR_MAP for all known blue chips (fast, no API call).
Falls back to yfinance for any promoted Pool 1 stocks not in the map.
Caps positions per sector at MAX_PER_SECTOR, dropping lowest-confidence excess.
"""
from __future__ import annotations
from config.settings import MAX_PER_SECTOR
from config.blue_chips import SECTOR_MAP

_CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


def _get_sector(ticker: str) -> str:
    if ticker in SECTOR_MAP:
        return SECTOR_MAP[ticker]
    # yfinance fallback for any promoted Pool 1 stock not in the static map
    try:
        import yfinance as yf
        return yf.Ticker(ticker).info.get("sector") or "Unknown"
    except Exception:
        return "Unknown"


def run(risk_output: dict) -> dict:
    approved = list(risk_output.get("approved_trades", []))

    if not approved:
        return {**risk_output, "sector_blocked": []}

    for trade in approved:
        trade["sector"] = _get_sector(trade["ticker"])

    by_sector: dict[str, list] = {}
    for trade in approved:
        by_sector.setdefault(trade["sector"], []).append(trade)

    kept, blocked = [], []
    for sector, trades in by_sector.items():
        if sector == "Unknown" or len(trades) <= MAX_PER_SECTOR:
            kept.extend(trades)
        else:
            sorted_trades = sorted(
                trades,
                key=lambda t: (
                    _CONFIDENCE_RANK.get(t.get("confidence", "LOW"), 0),
                    t.get("estimated_profit", 0),
                ),
                reverse=True,
            )
            kept.extend(sorted_trades[:MAX_PER_SECTOR])
            for t in sorted_trades[MAX_PER_SECTOR:]:
                blocked.append({
                    "ticker": t["ticker"],
                    "sector": sector,
                    "reason": f"Sector cap: {len(trades)} {sector} picks, max {MAX_PER_SECTOR}",
                })
            print(f"[sector_guard] {sector}: {len(trades)} picks → capped at {MAX_PER_SECTOR}, "
                  f"dropped {[t['ticker'] for t in sorted_trades[MAX_PER_SECTOR:]]}")

    return {
        **risk_output,
        "approved_trades":        kept,
        "total_estimated_profit": sum(t.get("estimated_profit", 0) for t in kept),
        "total_max_loss":         sum(t.get("max_loss", 0) for t in kept),
        "sector_blocked":         blocked,
    }
