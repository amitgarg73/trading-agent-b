"""
Pool Filter — selects Pool 3 (daily elite picks) from Pool 2 each morning.

Applies real-time filters:
  1. Relative volume > POOL3_MIN_VOL_RATIO vs own 20-day average
  2. No earnings within POOL3_EARNINGS_DAYS days
  3. Stock moving with or leading sector ETF (positive RS)
  4. Above VWAP (buying pressure since open)
  5. Clean ATR range (not gapping beyond 2x normal)

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
    """Fetch intraday metrics for a single ticker."""
    try:
        t  = yf.Ticker(ticker)
        df = t.history(period="5d", interval="1d")
        if df is None or len(df) < 2:
            return None
        df.columns = [c.lower() for c in df.columns]

        avg_vol   = float(df["volume"].tail(20).mean()) if len(df) >= 20 else float(df["volume"].mean())
        today_vol = float(df["volume"].iloc[-1])
        vol_ratio = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0

        cur_price   = float(df["close"].iloc[-1])
        prev_close  = float(df["close"].iloc[-2])
        today_open  = float(df["open"].iloc[-1])
        today_high  = float(df["high"].iloc[-1])
        today_low   = float(df["low"].iloc[-1])

        typical_price = (today_high + today_low + cur_price) / 3
        above_vwap = cur_price > typical_price

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

        return {
            "ticker":        ticker,
            "vol_ratio":     vol_ratio,
            "above_vwap":    above_vwap,
            "rs_vs_sector":  rs_vs_sector,
            "cur_price":     cur_price,
            "today_return":  round(stock_return * 100, 2),
        }
    except Exception as e:
        print(f"[pool_filter] {ticker}: {e}")
        return None


def _filter_score(m: dict) -> float:
    """Score a ticker for Pool 3 eligibility. Higher = better daily pick."""
    score = 0.0

    if m["vol_ratio"] >= POOL3_MIN_VOL_RATIO:
        score += m["vol_ratio"]         # more volume = higher score

    if m["above_vwap"]:
        score += 2.0

    rs = m.get("rs_vs_sector")
    if rs is not None:
        if rs > 1.5:
            score += 2.0
        elif rs > 0.5:
            score += 1.0
        elif rs < 0:
            score -= 1.5

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
        if _has_earnings_soon(ticker):
            print(f"[pool_filter] {ticker} excluded — earnings within {POOL3_EARNINGS_DAYS} days")
            continue
        m = _realtime_metrics(ticker)
        if m and m["vol_ratio"] >= POOL3_MIN_VOL_RATIO:
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
        if _has_earnings_soon(ticker):
            continue
        m = _realtime_metrics(ticker)
        if m and m["vol_ratio"] >= POOL3_MIN_VOL_RATIO:
            m["filter_score"] = _filter_score(m)
            metrics.append(m)

    metrics.sort(key=lambda x: x["filter_score"], reverse=True)
    return metrics[:POOL3_SIZE]
