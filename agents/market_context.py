"""
Market Context — fetches VIX, Fear & Greed, futures bias, sector rotation.
Same logic as Strategy A (self-contained, no shared import).
"""
from __future__ import annotations
import requests
import yfinance as yf

_SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLB", "XLRE", "XLU"]


def get() -> dict:
    context = {}

    # VIX
    try:
        vix = yf.Ticker("^VIX").history(period="2d")
        if not vix.empty:
            context["vix_level"] = round(float(vix["Close"].iloc[-1]), 1)
    except Exception:
        context["vix_level"] = None

    # Futures bias via SPY pre/post market
    try:
        spy = yf.Ticker("SPY").history(period="2d")
        if len(spy) >= 2:
            chg = (float(spy["Close"].iloc[-1]) - float(spy["Close"].iloc[-2])) / float(spy["Close"].iloc[-2])
            context["futures_bias"] = "BULLISH" if chg > 0.002 else ("BEARISH" if chg < -0.002 else "NEUTRAL")
            context["spy_change_pct"] = round(chg * 100, 2)
    except Exception:
        context["futures_bias"] = "NEUTRAL"

    # Fear & Greed (CNN)
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        if r.status_code == 200:
            data = r.json()
            context["fear_greed"] = int(data["fear_and_greed"]["score"])
            context["fear_greed_label"] = data["fear_and_greed"]["rating"]
    except Exception:
        context["fear_greed"] = 50
        context["fear_greed_label"] = "Neutral"

    # Sector rotation
    try:
        import pandas as pd
        raw = yf.download(_SECTOR_ETFS, period="2d", interval="1d",
                          progress=False, group_by="ticker")
        rotation = {}
        for etf in _SECTOR_ETFS:
            try:
                s = raw[etf]["Close"].dropna() if isinstance(raw.columns, pd.MultiIndex) else raw["Close"].dropna()
                if len(s) >= 2:
                    chg = (float(s.iloc[-1]) - float(s.iloc[-2])) / float(s.iloc[-2]) * 100
                    rotation[etf] = round(chg, 2)
            except Exception:
                pass
        context["sector_rotation"] = dict(sorted(rotation.items(), key=lambda x: x[1], reverse=True))
    except Exception:
        context["sector_rotation"] = {}

    print(f"[market_context] VIX={context.get('vix_level')} "
          f"F&G={context.get('fear_greed')} bias={context.get('futures_bias')}")
    if context.get("sector_rotation"):
        items = list(context["sector_rotation"].items())
        top = ", ".join(f"{k} {'+' if v >= 0 else ''}{v:.1f}%" for k, v in items[:3])
        print(f"[market_context] Sector leaders: {top}")
    return context


def get_regime_label(vix: float | None, fear_greed: int | None,
                     spy_change_pct: float | None) -> str:
    """
    Classify today's market regime for passive logging.
    No hard gates — observation only in Phase 1.

    FEAR:     VIX > 35 OR Fear&Greed < 15 OR SPY < -2%
    HIGH_VOL: VIX > 25 OR SPY move > 1.5% either direction
    TREND:    SPY move > 0.5% in one direction, VIX calm
    CHOPPY:   everything else
    """
    vix = vix or 0
    fg  = fear_greed or 50
    spy = spy_change_pct or 0

    if vix > 35 or fg < 15 or spy < -2.0:
        return "FEAR"
    if vix > 25 or abs(spy) > 1.5:
        return "HIGH_VOL"
    if abs(spy) > 0.5:
        return "TREND"
    return "CHOPPY"
