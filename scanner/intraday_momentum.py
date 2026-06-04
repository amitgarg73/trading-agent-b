"""
Intraday scanner — Option 2 market-participation signal for blue chips.

Trigger: (SPY up >= MIN_SPY_MOVE_PCT OR sector ETF up >= STRONG_SECTOR_THRESHOLD)
         AND stock above VWAP AND stock up >= MIN_INTRADAY_MOVE_PCT (0.5%).
On rotation days (e.g. semis +6%, SPY flat) the sector gate overrides SPY so
Pool 3 tech/semi names still get scanned.

Alpaca mode: snapshot API (today_pct_change, above_vwap, rs_vs_spy) + SPY/sector gate.
Simulation mode: yfinance 5-min data. No SPY gate (used for dev/backtest).
"""
from __future__ import annotations
from config.settings import (
    MIN_INTRADAY_MOVE_PCT, MIN_SPY_MOVE_PCT, SCORE_THRESHOLD, STRONG_SECTOR_THRESHOLD,
    STALE_MOVE_THRESHOLD_PCT, FRESH_MOMENTUM_MIN_PCT,
)

# Sector ETFs to check as SPY-gate override
_SECTOR_ETFS = ["XLK", "XLF", "XLV", "XLY", "XLE", "XLI"]


def _momentum_score(pct_change: float, rs_vs_spy: float | None) -> int:
    """
    Score a momentum candidate.
    2% move → score 3, 4% → 4, 6% → 5, 8% → 6, 10% → 7, 15% → 8, 20%+ → 9
    RS vs SPY ≥ 2 adds +1 bonus.
    Always returns at least SCORE_THRESHOLD so it survives strategy filtering.
    """
    base = max(SCORE_THRESHOLD, 3 + int(pct_change / 2))
    if rs_vs_spy and rs_vs_spy >= 2.0:
        base += 1
    return min(10, base)


def scan_alpaca(universe: list[str]) -> list[dict]:
    """
    Option 2 market-participation scan via Alpaca snapshot API.
    Gate 1: SPY >= MIN_SPY_MOVE_PCT OR any sector ETF >= STRONG_SECTOR_THRESHOLD.
            Sector override catches rotation days (e.g. semis +6%, SPY flat).
    Gate 2: stock above VWAP AND up >= MIN_INTRADAY_MOVE_PCT (0.5%) — confirms participation.
    """
    from agents import alpaca_broker

    # Include SPY + sector ETFs for gate check
    fetch = list(set(universe + ["SPY"] + _SECTOR_ETFS))
    signals = alpaca_broker.get_intraday_signals(fetch)
    live    = alpaca_broker.get_live_prices([t for t in signals if t not in {"SPY"} | set(_SECTOR_ETFS)])

    # Gate: SPY positive OR any sector ETF up strongly (rotation day)
    spy_pct     = (signals.get("SPY") or {}).get("today_pct_change") or 0
    sector_pcts = {etf: (signals.get(etf) or {}).get("today_pct_change") or 0 for etf in _SECTOR_ETFS}
    best_sector = max(sector_pcts.values()) if sector_pcts else 0
    best_etf    = max(sector_pcts, key=sector_pcts.get) if sector_pcts else ""

    spy_ok    = spy_pct >= MIN_SPY_MOVE_PCT
    sector_ok = best_sector >= STRONG_SECTOR_THRESHOLD

    if not spy_ok and not sector_ok:
        print(f"        [intraday-b] SPY {spy_pct:+.2f}%, best sector {best_etf} {best_sector:+.2f}% "
              f"— market gate not met (SPY need {MIN_SPY_MOVE_PCT:+.1f}% or sector need +{STRONG_SECTOR_THRESHOLD:.1f}%)")
        return []

    if sector_ok and not spy_ok:
        print(f"        [intraday-b] SPY {spy_pct:+.2f}% — gate overridden by {best_etf} {best_sector:+.2f}%")

    candidates = []
    for ticker, sig in signals.items():
        if ticker in {"SPY"} | set(_SECTOR_ETFS):
            continue
        pct        = sig.get("today_pct_change") or 0
        above_vwap = sig.get("above_vwap", False)
        rs         = sig.get("rs_vs_spy")

        if pct < MIN_INTRADAY_MOVE_PCT:
            continue
        if pct > 30:
            continue
        if not above_vwap:
            continue

        pct_15m = sig.get("change_pct_15m", 0.0)
        if pct >= STALE_MOVE_THRESHOLD_PCT and pct_15m < FRESH_MOMENTUM_MIN_PCT:
            print(f"        [intraday-b] {ticker} stale: up {pct:.1f}% today "
                  f"but only {pct_15m:+.2f}% in last 15m — skipping")
            continue

        score = _momentum_score(pct, rs)
        price = live.get(ticker) or sig.get("vwap") or 0

        candidates.append({
            "ticker":           ticker,
            "technical_score":  score,
            "pool":             2,
            "action":           "BUY",
            "current_price":    price,
            "entry_price":      price,
            "above_vwap":       above_vwap,
            "today_pct_change": pct,
            "change_pct_15m":   pct_15m,
            "rs_vs_spy":        rs,
            "vwap":             sig.get("vwap"),
            "rsi":              50,
            "volume_ratio":     rs or 1.0,
            "signal_type":      "INTRADAY_MOMENTUM",
        })

    candidates.sort(key=lambda x: (-(x.get("rs_vs_spy") or 0), -x["today_pct_change"]))
    return candidates


def scan_simulation(universe: list[str]) -> list[dict]:
    """
    Simulation fallback: use yfinance 5-min data to find today's movers.
    Pool 3 is ≤10 stocks so this is fast with no batching needed.
    """
    import yfinance as yf

    candidates = []
    for ticker in universe:
        try:
            df = yf.download(
                ticker,
                period="1d",
                interval="5m",
                auto_adjust=True,
                progress=False,
            )
            if df is None or df.empty:
                continue

            open_px    = float(df["Open"].iloc[0])
            current    = float(df["Close"].iloc[-1])
            vwap_proxy = float(df["Close"].mean())

            if open_px <= 0:
                continue

            pct = (current - open_px) / open_px * 100
            if pct < MIN_INTRADAY_MOVE_PCT or pct > 30:
                continue
            if current < vwap_proxy:
                continue

            score = _momentum_score(pct, None)
            candidates.append({
                "ticker":           ticker,
                "technical_score":  score,
                "pool":             2,
                "action":           "BUY",
                "current_price":    round(current, 2),
                "entry_price":      round(current, 2),
                "above_vwap":       True,
                "today_pct_change": round(pct, 2),
                "rs_vs_spy":        None,
                "vwap":             round(vwap_proxy, 2),
                "rsi":              50,
                "volume_ratio":     1.0,
                "signal_type":      "INTRADAY_MOMENTUM",
            })
        except Exception:
            continue

    candidates.sort(key=lambda x: -x["today_pct_change"])
    return candidates


def scan(universe: list[str], broker: str = "simulation") -> list[dict]:
    """Entry point: returns momentum candidates for the given Pool 3 universe."""
    try:
        if broker == "alpaca":
            return scan_alpaca(universe)
        return scan_simulation(universe)
    except Exception as e:
        print(f"        ⚠️  Intraday momentum scan error: {e}")
        return []
