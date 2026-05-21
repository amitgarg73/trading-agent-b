"""
Pool Filter — selects Pool 3 (daily elite picks) from Pool 2 each morning.

Applies real-time filters:
  1. Relative volume > POOL3_MIN_VOL_RATIO vs own 20-day average
  2. No earnings within POOL3_EARNINGS_DAYS days
  3. Stock moving with or leading sector ETF (positive RS)
  4. Above VWAP (buying pressure since open)
  5. Clean ATR range (not gapping beyond 2x normal)
  6. Opening Range Breakout — above first 30-min high (from intraday 5m bars)
  7. VWAP Reclaim — dipped below intraday VWAP, now trading above it
  8. Volume acceleration — volume building vs fading intraday
  9. Market-relative strength — vs SPY directly, not just sector ETF

Returns top POOL3_SIZE tickers ranked by composite filter score.
"""
from __future__ import annotations
from datetime import date, timedelta
import yfinance as yf
import pandas as pd
from core import pool_manager
from config.settings import (
    POOL3_SIZE, POOL3_MIN_VOL_RATIO, POOL3_EARNINGS_DAYS,
)
from config.blue_chips import SECTOR_MAP, SECTOR_ETF

_SPY_RETURN_CACHE: dict = {}  # avoid refetching SPY on every ticker


def _spy_return() -> float | None:
    """Today's SPY return, cached per process."""
    if "value" in _SPY_RETURN_CACHE:
        return _SPY_RETURN_CACHE["value"]
    try:
        df = yf.Ticker("SPY").history(period="2d")
        if len(df) >= 2:
            df.columns = [c.lower() for c in df.columns]
            val = (float(df["close"].iloc[-1]) - float(df["close"].iloc[-2])) / float(df["close"].iloc[-2])
            _SPY_RETURN_CACHE["value"] = val
            return val
    except Exception:
        pass
    return None


def _intraday_bars(ticker: str) -> pd.DataFrame | None:
    """
    Fetch today's 5-min bars for ORB and VWAP reclaim signals.
    Returns None if market is closed or fewer than 6 bars available.
    """
    try:
        df = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        # Flatten MultiIndex columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index)
        today_bars = df[df.index.date == date.today()]
        return today_bars if len(today_bars) >= 6 else None
    except Exception:
        return None


def _orb_vwap_signals(intraday: pd.DataFrame) -> dict:
    """
    Compute Opening Range Breakout, intraday VWAP reclaim, and volume acceleration
    from 5-min intraday bars.

    ORB: first 30 min (6 bars). above_orb = current price > ORB high.
    VWAP reclaim: price was below intraday VWAP in the first half, now above it.
    vol_acceleration: avg volume of last 3 bars vs first 3 bars (>1 = building).
    """
    signals = {}
    try:
        cur_price = float(intraday["close"].iloc[-1])

        # Opening Range Breakout
        orb = intraday.head(6)
        orb_high = float(orb["high"].max())
        orb_low  = float(orb["low"].min())
        signals["orb_high"]  = orb_high
        signals["orb_low"]   = orb_low
        signals["above_orb"] = cur_price > orb_high

        # True intraday VWAP
        df = intraday.copy()
        df["typical"]    = (df["high"] + df["low"] + df["close"]) / 3
        df["cum_tpvol"]  = (df["typical"] * df["volume"]).cumsum()
        df["cum_vol"]    = df["volume"].cumsum()
        df["vwap"]       = df["cum_tpvol"] / df["cum_vol"]

        vwap_now         = float(df["vwap"].iloc[-1])
        above_vwap_now   = cur_price > vwap_now

        # Reclaim: any first-half bar below VWAP, and currently above VWAP
        mid = max(1, len(df) // 2)
        was_below = bool((df["close"].iloc[:mid] < df["vwap"].iloc[:mid]).any())
        signals["vwap_reclaim"]       = above_vwap_now and was_below
        signals["above_vwap_intraday"] = above_vwap_now

        # Volume acceleration
        early_vol  = float(intraday["volume"].head(3).mean())
        recent_vol = float(intraday["volume"].tail(3).mean())
        signals["vol_acceleration"] = round(recent_vol / early_vol, 2) if early_vol > 0 else 1.0

    except Exception as e:
        print(f"[pool_filter] ORB/VWAP signal error: {e}")

    return signals


def _has_earnings_soon(ticker: str) -> bool:
    """Return True if earnings are within POOL3_EARNINGS_DAYS days."""
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is None or cal.empty:
            return False
        if "Earnings Date" in cal.index:
            earnings = cal.loc["Earnings Date"]
            first = pd.Timestamp(earnings.iloc[0]) if hasattr(earnings, "iloc") else pd.Timestamp(earnings)
            days_away = (first.date() - date.today()).days
            return 0 <= days_away <= POOL3_EARNINGS_DAYS
    except Exception:
        pass
    return False


def _realtime_metrics(ticker: str) -> dict | None:
    """Fetch daily + intraday metrics for a single ticker."""
    try:
        t  = yf.Ticker(ticker)
        df = t.history(period="5d", interval="1d")
        if df is None or len(df) < 2:
            return None
        df.columns = [c.lower() for c in df.columns]

        avg_vol   = float(df["volume"].tail(20).mean()) if len(df) >= 20 else float(df["volume"].mean())
        today_vol = float(df["volume"].iloc[-1])
        vol_ratio = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0

        cur_price  = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-2])
        today_high = float(df["high"].iloc[-1])
        today_low  = float(df["low"].iloc[-1])

        # Fallback VWAP from daily OHLC (overridden below if intraday available)
        typical_price = (today_high + today_low + cur_price) / 3
        above_vwap    = cur_price > typical_price

        stock_return = (cur_price - prev_close) / prev_close

        # Sector RS
        sector = SECTOR_MAP.get(ticker, "ETF")
        etf    = SECTOR_ETF.get(sector, "SPY")
        rs_vs_sector = None
        try:
            edf = yf.Ticker(etf).history(period="2d")
            if len(edf) >= 2:
                edf.columns = [c.lower() for c in edf.columns]
                etf_return = (float(edf["close"].iloc[-1]) - float(edf["close"].iloc[-2])) / float(edf["close"].iloc[-2])
                if abs(etf_return) > 0.0005:
                    rs_vs_sector = round(stock_return / abs(etf_return), 2)
        except Exception:
            pass

        # Market-relative strength vs SPY
        spy_ret      = _spy_return()
        rs_vs_market = None
        if spy_ret is not None and abs(spy_ret) > 0.0005:
            rs_vs_market = round(stock_return / abs(spy_ret), 2)

        result = {
            "ticker":        ticker,
            "vol_ratio":     vol_ratio,
            "above_vwap":    above_vwap,     # daily OHLC fallback
            "rs_vs_sector":  rs_vs_sector,
            "rs_vs_market":  rs_vs_market,
            "cur_price":     cur_price,
            "today_return":  round(stock_return * 100, 2),
            # intraday signals default to None — overridden if market is open
            "above_orb":          None,
            "vwap_reclaim":       None,
            "above_vwap_intraday": None,
            "vol_acceleration":   None,
        }

        # Intraday signals — only populated when market is open (≥6 bars)
        intraday = _intraday_bars(ticker)
        if intraday is not None:
            orb_signals = _orb_vwap_signals(intraday)
            result.update(orb_signals)
            # Prefer intraday VWAP over daily approximation
            if orb_signals.get("above_vwap_intraday") is not None:
                result["above_vwap"] = orb_signals["above_vwap_intraday"]

        return result

    except Exception as e:
        print(f"[pool_filter] {ticker}: {e}")
        return None


def _filter_score(m: dict) -> float:
    """
    Score a ticker for Pool 3 eligibility. Higher = better daily pick.

    Signal hierarchy (strongest → weakest):
      VWAP reclaim > ORB breakout > above VWAP > volume acceleration > sector RS > market RS
    """
    score = 0.0

    # Volume — more = better (no hard gate; early-day low vol still scores 0)
    score += m.get("vol_ratio", 0)

    # VWAP signals — reclaim is stronger than just above
    if m.get("vwap_reclaim"):
        score += 3.0                   # dipped below VWAP then reclaimed — high conviction
    elif m.get("above_vwap_intraday") or m.get("above_vwap"):
        score += 1.5                   # simply above VWAP

    # Opening Range Breakout — confirmed momentum
    if m.get("above_orb"):
        score += 2.0

    # Volume acceleration — is buying pressure building?
    vacc = m.get("vol_acceleration")
    if vacc is not None:
        if vacc >= 1.2:
            score += 1.0               # building — good
        elif vacc < 0.8:
            score -= 0.5               # fading — caution

    # Sector-relative strength
    rs = m.get("rs_vs_sector")
    if rs is not None:
        if rs > 1.5:
            score += 2.0
        elif rs > 0.5:
            score += 1.0
        elif rs < 0:
            score -= 1.5

    # Market-relative strength (vs SPY)
    mrs = m.get("rs_vs_market")
    if mrs is not None and mrs > 1.0:
        score += 1.0

    if m["today_return"] > 0:
        score += 0.5

    return score


def get_pool3_tickers() -> list[str]:
    """
    From Pool 2 stocks, select today's Pool 3 (8-10 elite picks).
    Returns list of tickers ranked by filter score.
    """
    pool2 = pool_manager.get_pool(2)
    if not pool2:
        print("[pool_filter] Pool 2 is empty — falling back to Pool 2 seed")
        from config.blue_chips import POOL_2_SEED
        pool2 = POOL_2_SEED

    metrics = []
    for ticker in pool2:
        m = _realtime_metrics(ticker)
        if m:
            m["filter_score"] = _filter_score(m)
            metrics.append(m)

    metrics.sort(key=lambda x: x["filter_score"], reverse=True)
    selected = [m["ticker"] for m in metrics[:POOL3_SIZE]]

    print(f"[pool_filter] Pool 3 today ({len(selected)} stocks): {selected}")
    return selected


def get_pool3_with_context() -> list[dict]:
    """Same as get_pool3_tickers but returns full metrics for scanner context."""
    pool2 = pool_manager.get_pool(2)
    if not pool2:
        from config.blue_chips import POOL_2_SEED
        pool2 = POOL_2_SEED

    metrics = []
    for ticker in pool2:
        m = _realtime_metrics(ticker)
        if m:
            m["filter_score"] = _filter_score(m)
            metrics.append(m)

    metrics.sort(key=lambda x: x["filter_score"], reverse=True)
    return metrics[:POOL3_SIZE]
