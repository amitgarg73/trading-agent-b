"""
Market Context — fetches VIX, Fear & Greed, futures bias, sector rotation.
Same logic as Strategy A (self-contained, no shared import).
"""
from __future__ import annotations
import requests
import yfinance as yf

_SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLB", "XLRE", "XLU"]


def _fetch_spy_change() -> tuple[str, float | None]:
    """Return (futures_bias, spy_change_pct) via Alpaca daily bars."""
    try:
        from datetime import datetime, timedelta
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from agents.alpaca_broker import _dclient
        req  = StockBarsRequest(symbol_or_symbols="SPY", timeframe=TimeFrame.Day,
                                start=datetime.utcnow() - timedelta(days=5),
                                end=datetime.utcnow())
        bars = _dclient().get_stock_bars(req).data.get("SPY") or []
        if len(bars) >= 2:
            prev = bars[-2].close
            curr = bars[-1].close
            chg  = (curr - prev) / prev
            bias = "BULLISH" if chg > 0.002 else ("BEARISH" if chg < -0.002 else "NEUTRAL")
            return bias, round(chg * 100, 2)
    except Exception:
        pass
    return "NEUTRAL", None


def _fetch_sector_rotation() -> dict:
    """Return {ETF: change_pct} sorted best→worst via Alpaca daily bars."""
    try:
        from datetime import datetime, timedelta
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from agents.alpaca_broker import _dclient
        req  = StockBarsRequest(symbol_or_symbols=_SECTOR_ETFS, timeframe=TimeFrame.Day,
                                start=datetime.utcnow() - timedelta(days=5),
                                end=datetime.utcnow())
        bars = _dclient().get_stock_bars(req).data
        rotation = {}
        for etf in _SECTOR_ETFS:
            etf_bars = bars.get(etf) or []
            if len(etf_bars) >= 2:
                prev = etf_bars[-2].close
                curr = etf_bars[-1].close
                rotation[etf] = round((curr - prev) / prev * 100, 2)
        return dict(sorted(rotation.items(), key=lambda x: x[1], reverse=True))
    except Exception:
        return {}


def get() -> dict:
    context = {}

    # VIX — Alpaca doesn't carry ^VIX; keep on yfinance (single call, low rate-limit risk)
    try:
        vix = yf.Ticker("^VIX").history(period="2d")
        if not vix.empty:
            context["vix_level"] = round(float(vix["Close"].iloc[-1]), 1)
    except Exception:
        context["vix_level"] = None

    # SPY recent momentum via Alpaca
    bias, spy_chg = _fetch_spy_change()
    context["futures_bias"]    = bias
    context["spy_change_pct"]  = spy_chg

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

    # Sector rotation via Alpaca
    context["sector_rotation"] = _fetch_sector_rotation()

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
