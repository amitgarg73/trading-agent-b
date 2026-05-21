"""
Intraday momentum scanner — finds Pool 3 stocks already moving strongly today.

Logic:
- Alpaca mode: uses snapshot API (today_pct_change, above_vwap, rs_vs_spy)
- Simulation mode: uses yfinance 5-min data for today's open → current move

Returns candidates in the same format as scanner.run_scan() so they can be
passed directly into strategy.select_trades() for intraday entry decisions.

Blue chip note: MIN_INTRADAY_MOVE_PCT is 2.0 (vs 4.0 in Strategy A) because
blue chips move less but with higher conviction — lower threshold is appropriate.
"""
from __future__ import annotations
from config.settings import MIN_INTRADAY_MOVE_PCT, SCORE_THRESHOLD


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
    Fetch intraday signals for Pool 3 via Alpaca snapshot API.
    Returns candidates that are up >= MIN_INTRADAY_MOVE_PCT, above VWAP,
    and not too extended (< 30%).
    """
    from agents import alpaca_broker

    signals = alpaca_broker.get_intraday_signals(universe)
    live    = alpaca_broker.get_live_prices(list(signals.keys()))

    candidates = []
    for ticker, sig in signals.items():
        pct        = sig.get("today_pct_change") or 0
        above_vwap = sig.get("above_vwap", False)
        rs         = sig.get("rs_vs_spy")

        if pct < MIN_INTRADAY_MOVE_PCT:
            continue
        if pct > 30:
            continue
        if not above_vwap:
            continue

        score = _momentum_score(pct, rs)
        price = live.get(ticker) or sig.get("vwap") or 0

        candidates.append({
            "ticker":           ticker,
            "technical_score":  score,
            "action":           "BUY",
            "current_price":    price,
            "entry_price":      price,
            "above_vwap":       above_vwap,
            "today_pct_change": pct,
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
