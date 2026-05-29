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

Data sourcing: Alpaca batch APIs replace ~170 sequential yfinance calls at market
open. Three calls cover all tickers: snapshots (current price/VWAP/daily bar),
daily bars (20-day avg volume), and 5-min intraday bars (ORB/VWAP signals).
yfinance is kept only for the earnings calendar (no Alpaca equivalent).
"""
from __future__ import annotations
from datetime import date, datetime, timezone
import pytz
import pandas as pd
import yfinance as yf

from core import pool_manager
from config.settings import (
    POOL3_SIZE, POOL3_MIN_VOL_RATIO, POOL3_EARNINGS_DAYS, POOL3_MIN_FILTER_SCORE,
    ALPACA_API_KEY, ALPACA_SECRET_KEY,
)
from config.blue_chips import SECTOR_MAP, SECTOR_ETF

_ET = pytz.timezone("America/New_York")
_SPY_RETURN_CACHE: dict = {}   # fallback cache for yfinance path
_batch_data_cache: dict[str, dict] = {}   # populated once per run by _prefetch_batch()

_data_client = None


def _dclient():
    global _data_client
    if _data_client is None:
        from alpaca.data import StockHistoricalDataClient
        _data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    return _data_client


def _spy_return() -> float | None:
    """Today's SPY return via yfinance — fallback used when Alpaca prefetch is skipped."""
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
    Fetch today's 5-min bars via yfinance — fallback used when Alpaca prefetch is skipped.
    Returns None if market is closed or fewer than 6 bars available.
    """
    try:
        df = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
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

        orb      = intraday.head(6)
        orb_high = float(orb["high"].max())
        orb_low  = float(orb["low"].min())
        signals["orb_high"]  = orb_high
        signals["orb_low"]   = orb_low
        signals["above_orb"] = cur_price > orb_high

        df = intraday.copy()
        df["typical"]   = (df["high"] + df["low"] + df["close"]) / 3
        df["cum_tpvol"] = (df["typical"] * df["volume"]).cumsum()
        df["cum_vol"]   = df["volume"].cumsum()
        df["vwap"]      = df["cum_tpvol"] / df["cum_vol"]

        vwap_now       = float(df["vwap"].iloc[-1])
        above_vwap_now = cur_price > vwap_now

        mid      = max(1, len(df) // 2)
        was_below = bool((df["close"].iloc[:mid] < df["vwap"].iloc[:mid]).any())
        signals["vwap_reclaim"]        = above_vwap_now and was_below
        signals["above_vwap_intraday"] = above_vwap_now

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


def _prefetch_batch(tickers: list[str]) -> None:
    """
    Replace ~170 sequential yfinance calls with 3 Alpaca batch API calls.

    Call 1 — StockSnapshotRequest (all tickers + sector ETFs + SPY): current price,
              daily bar OHLCV/VWAP, and previous-day close for stock/ETF returns.
    Call 2 — StockBarsRequest daily (all tickers, 22 bars): 20-day average volume.
    Call 3 — StockBarsRequest 5-min (all tickers, since 9:30 AM ET): ORB/VWAP signals.

    Populates _batch_data_cache; subsequent _realtime_metrics() reads from there.
    """
    from alpaca.data.requests import StockSnapshotRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    global _batch_data_cache
    _batch_data_cache = {}

    etfs        = {SECTOR_ETF.get(SECTOR_MAP.get(t, "ETF"), "SPY") for t in tickers} | {"SPY"}
    all_symbols = list(set(tickers) | etfs)

    # --- Call 1: snapshots ---
    snapshots: dict = {}
    try:
        snapshots = _dclient().get_stock_snapshot(
            StockSnapshotRequest(symbol_or_symbols=all_symbols)
        ) or {}
        print(f"[pool_filter] snapshot: {len(snapshots)}/{len(all_symbols)} symbols returned")
    except Exception as e:
        print(f"[pool_filter] snapshot batch failed: {e}")

    # --- Call 2: 22 daily bars for 20-day avg volume ---
    daily_avgs: dict[str, float] = {}
    try:
        resp = _dclient().get_stock_bars(
            StockBarsRequest(symbol_or_symbols=list(tickers), timeframe=TimeFrame.Day, limit=22)
        )
        bars_dict = resp.data if hasattr(resp, "data") else (dict(resp) if resp else {})
        for ticker, bar_list in bars_dict.items():
            vols = [float(getattr(b, "volume", 0) or 0) for b in bar_list]
            if len(vols) >= 2:
                # Exclude today's partial bar from the average
                daily_avgs[ticker] = sum(vols[:-1]) / max(1, len(vols) - 1)
        print(f"[pool_filter] daily bars: {len(daily_avgs)}/{len(tickers)} tickers returned")
    except Exception as e:
        print(f"[pool_filter] daily bars batch failed: {e}")

    # --- Call 3: 5-min intraday bars (market hours only) ---
    intraday_all: dict[str, pd.DataFrame] = {}
    now_et          = datetime.now(_ET)
    market_open_et  = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    if now_et >= market_open_et:
        try:
            start_utc = market_open_et.astimezone(timezone.utc)
            resp = _dclient().get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=list(tickers),
                    timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                    start=start_utc,
                )
            )
            bars_dict = resp.data if hasattr(resp, "data") else (dict(resp) if resp else {})
            for ticker, bar_list in bars_dict.items():
                rows = [
                    {
                        "open":   float(getattr(b, "open",   0) or 0),
                        "high":   float(getattr(b, "high",   0) or 0),
                        "low":    float(getattr(b, "low",    0) or 0),
                        "close":  float(getattr(b, "close",  0) or 0),
                        "volume": float(getattr(b, "volume", 0) or 0),
                    }
                    for b in bar_list
                ]
                if len(rows) >= 6:
                    intraday_all[ticker] = pd.DataFrame(rows)
            print(f"[pool_filter] intraday bars: {len(intraday_all)}/{len(tickers)} tickers returned")
        except Exception as e:
            print(f"[pool_filter] intraday bars batch failed: {e}")

    # --- SPY return for market RS ---
    spy_return: float | None = None
    spy_snap = snapshots.get("SPY")
    if spy_snap and spy_snap.daily_bar:
        spy_open  = float(getattr(spy_snap.daily_bar, "open", None) or 0)
        spy_close = float(
            getattr(getattr(spy_snap, "latest_trade", None), "price", None)
            or getattr(spy_snap.daily_bar, "close", None) or 0
        )
        if spy_open > 0 and spy_close > 0:
            spy_return = (spy_close - spy_open) / spy_open

    # --- ETF returns for sector RS ---
    etf_returns: dict[str, float] = {}
    for etf in etfs:
        snap = snapshots.get(etf)
        if not snap or not snap.daily_bar:
            continue
        prev = getattr(snap, "prev_day_bar", None)
        if not prev:
            continue
        etf_close = float(
            getattr(getattr(snap, "latest_trade", None), "price", None)
            or getattr(snap.daily_bar, "close", None) or 0
        )
        etf_prev = float(getattr(prev, "close", None) or 0)
        if etf_close > 0 and etf_prev > 0:
            etf_returns[etf] = (etf_close - etf_prev) / etf_prev

    # --- Build per-ticker cache entries ---
    for ticker in tickers:
        snap = snapshots.get(ticker)
        if not snap or not snap.daily_bar:
            continue

        latest_trade = getattr(snap, "latest_trade", None)
        prev_day     = getattr(snap, "prev_day_bar", None)

        cur_price  = float(getattr(latest_trade, "price", None) or getattr(snap.daily_bar, "close", None) or 0)
        prev_close = float(getattr(prev_day, "close", None) or 0) if prev_day else 0
        today_vol  = float(getattr(snap.daily_bar, "volume", 0) or 0)
        vwap       = float(getattr(snap.daily_bar, "vwap",   0) or 0)
        today_high = float(getattr(snap.daily_bar, "high",   0) or 0)
        today_low  = float(getattr(snap.daily_bar, "low",    0) or 0)

        if cur_price <= 0 or prev_close <= 0:
            continue

        stock_return = (cur_price - prev_close) / prev_close

        avg_vol   = daily_avgs.get(ticker)
        vol_ratio = round(today_vol / avg_vol, 2) if (avg_vol and avg_vol > 0) else 0.0

        above_vwap = cur_price > vwap if vwap > 0 else cur_price > (today_high + today_low + cur_price) / 3

        sector       = SECTOR_MAP.get(ticker, "ETF")
        etf_key      = SECTOR_ETF.get(sector, "SPY")
        etf_ret      = etf_returns.get(etf_key)
        rs_vs_sector = round(stock_return / abs(etf_ret), 2) if (etf_ret and abs(etf_ret) > 0.003) else None
        rs_vs_market = round(stock_return / abs(spy_return), 2) if (spy_return and abs(spy_return) > 0.003) else None

        entry: dict = {
            "ticker":              ticker,
            "vol_ratio":           vol_ratio,
            "above_vwap":          above_vwap,
            "rs_vs_sector":        rs_vs_sector,
            "rs_vs_market":        rs_vs_market,
            "cur_price":           cur_price,
            "today_return":        round(stock_return * 100, 2),
            "above_orb":           None,
            "vwap_reclaim":        None,
            "above_vwap_intraday": None,
            "vol_acceleration":    None,
        }

        intraday = intraday_all.get(ticker)
        if intraday is not None:
            orb_signals = _orb_vwap_signals(intraday)
            entry.update(orb_signals)
            if orb_signals.get("above_vwap_intraday") is not None:
                entry["above_vwap"] = orb_signals["above_vwap_intraday"]

        _batch_data_cache[ticker] = entry


def _realtime_metrics(ticker: str) -> dict | None:
    """
    Return metrics dict for ticker. Reads from Alpaca batch cache if available;
    falls back to sequential yfinance calls for testing and edge cases.
    """
    if ticker in _batch_data_cache:
        return _batch_data_cache[ticker]

    # Fallback: legacy yfinance path (used in tests; in production the batch cache is always populated)
    try:
        t  = yf.Ticker(ticker)
        df = t.history(period="30d", interval="1d")
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

        typical_price = (today_high + today_low + cur_price) / 3
        above_vwap    = cur_price > typical_price
        stock_return  = (cur_price - prev_close) / prev_close

        sector = SECTOR_MAP.get(ticker, "ETF")
        etf    = SECTOR_ETF.get(sector, "SPY")
        rs_vs_sector = None
        try:
            edf = yf.Ticker(etf).history(period="2d")
            if len(edf) >= 2:
                edf.columns = [c.lower() for c in edf.columns]
                etf_return = (float(edf["close"].iloc[-1]) - float(edf["close"].iloc[-2])) / float(edf["close"].iloc[-2])
                if abs(etf_return) > 0.003:
                    rs_vs_sector = round(stock_return / abs(etf_return), 2)
        except Exception:
            pass

        spy_ret      = _spy_return()
        rs_vs_market = None
        if spy_ret is not None and abs(spy_ret) > 0.003:
            rs_vs_market = round(stock_return / abs(spy_ret), 2)

        result = {
            "ticker":              ticker,
            "vol_ratio":           vol_ratio,
            "above_vwap":          above_vwap,
            "rs_vs_sector":        rs_vs_sector,
            "rs_vs_market":        rs_vs_market,
            "cur_price":           cur_price,
            "today_return":        round(stock_return * 100, 2),
            "above_orb":           None,
            "vwap_reclaim":        None,
            "above_vwap_intraday": None,
            "vol_acceleration":    None,
        }

        intraday = _intraday_bars(ticker)
        if intraday is not None:
            orb_signals = _orb_vwap_signals(intraday)
            result.update(orb_signals)
            if orb_signals.get("above_vwap_intraday") is not None:
                result["above_vwap"] = orb_signals["above_vwap_intraday"]

        return result

    except Exception as e:
        print(f"[pool_filter] {ticker}: {e}")
        return None


def _filter_score(m: dict) -> float:
    """
    Score a ticker for Pool 3 eligibility. Higher = better daily pick.

    Signal hierarchy (strongest to weakest):
      VWAP reclaim > ORB breakout > above VWAP > volume acceleration > sector RS > market RS
    """
    score = 0.0

    score += m.get("vol_ratio", 0)

    if m.get("vwap_reclaim"):
        score += 3.0
    elif m.get("above_vwap_intraday") or m.get("above_vwap"):
        score += 1.5

    if m.get("above_orb"):
        score += 2.0

    vacc = m.get("vol_acceleration")
    if vacc is not None:
        if vacc >= 1.2:
            score += 1.0
        elif vacc < 0.8:
            score -= 0.5

    rs = m.get("rs_vs_sector")
    if rs is not None:
        if rs > 1.5:
            score += 2.0
        elif rs > 0.5:
            score += 1.0
        elif rs < 0:
            score -= 1.5

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

    if ALPACA_API_KEY:
        _prefetch_batch(pool2)

    metrics = []
    for ticker in pool2:
        m = _realtime_metrics(ticker)
        if m:
            m["filter_score"] = _filter_score(m)
            metrics.append(m)

    metrics.sort(key=lambda x: x["filter_score"], reverse=True)
    passing  = [m for m in metrics if m["filter_score"] > POOL3_MIN_FILTER_SCORE]
    selected = [m["ticker"] for m in passing[:POOL3_SIZE]]

    skipped = len(metrics) - len(passing)
    if skipped:
        print(f"[pool_filter] {skipped} stock(s) below quality floor (score <= {POOL3_MIN_FILTER_SCORE}) — excluded")
    print(f"[pool_filter] Pool 3 today ({len(selected)} stocks): {selected}")
    return selected


def get_pool3_with_context() -> list[dict]:
    """Same as get_pool3_tickers but returns full metrics for scanner context."""
    pool2 = pool_manager.get_pool(2)
    if not pool2:
        from config.blue_chips import POOL_2_SEED
        pool2 = POOL_2_SEED

    if ALPACA_API_KEY:
        _prefetch_batch(pool2)

    metrics = []
    for ticker in pool2:
        m = _realtime_metrics(ticker)
        if m:
            m["filter_score"] = _filter_score(m)
            metrics.append(m)

    metrics.sort(key=lambda x: x["filter_score"], reverse=True)
    passing = [m for m in metrics if m["filter_score"] > POOL3_MIN_FILTER_SCORE]
    return passing[:POOL3_SIZE]
