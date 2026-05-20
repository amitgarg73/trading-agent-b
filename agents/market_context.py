"""
Market Context — fetches VIX, Fear & Greed, futures bias.
Same logic as Strategy A (self-contained, no shared import).
"""
from __future__ import annotations
import requests
import yfinance as yf


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

    print(f"[market_context] VIX={context.get('vix_level')} "
          f"F&G={context.get('fear_greed')} bias={context.get('futures_bias')}")
    return context
