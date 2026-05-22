"""
Strategy B scanner — behavioral scoring for blue chip stocks.

Scores each ticker on both technical signals (same as Strategy A)
and behavioral signals unique to Strategy B:
  - VWAP position and reclaim behavior
  - ATR consistency (is today's range normal or extended?)
  - Relative strength vs sector ETF
  - Opening range behavior
  - Volume profile

Returns candidates with score >= SCORE_THRESHOLD for strategy agent.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import yfinance as yf
import pandas as pd
import ta
from config.settings import (
    RSI_OVERSOLD, RSI_OVERBOUGHT, MIN_VOLUME_RATIO,
    MIN_PRICE, MIN_AVG_VOLUME, SCORE_THRESHOLD,
)
from config.blue_chips import SECTOR_MAP, SECTOR_ETF


def _fetch(ticker: str) -> tuple[dict, pd.DataFrame | None]:
    for attempt in range(2):
        try:
            t = yf.Ticker(ticker)
            info = t.info
            df   = t.history(period="3mo")
            if df.empty or len(df) < 20:
                return {}, None
            df.columns = [c.lower() for c in df.columns]
            return info, df
        except Exception:
            if attempt == 0:
                import time; time.sleep(2)
    return {}, None


def _fetch_sector_return(ticker: str) -> float | None:
    """Today's return for the sector ETF of this ticker."""
    sector  = SECTOR_MAP.get(ticker, "ETF")
    etf     = SECTOR_ETF.get(sector, "SPY")
    try:
        df = yf.Ticker(etf).history(period="2d")
        if len(df) < 2:
            return None
        df.columns = [c.lower() for c in df.columns]
        return float((df["close"].iloc[-1] - df["close"].iloc[-2]) / df["close"].iloc[-2])
    except Exception:
        return None


def _behavioral_score(ticker: str, df: pd.DataFrame, info: dict) -> dict:
    """
    Compute behavioral signals on top of technical signals.
    Returns dict of behavioral metrics and a behavior_score (-5 to +5).
    """
    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]
    score  = 0
    signals = []

    # ATR consistency — is today's range normal?
    atr_14 = ta.volatility.AverageTrueRange(high, low, close, 14).average_true_range()
    atr_now = float(high.iloc[-1] - low.iloc[-1])
    atr_avg = float(atr_14.iloc[-1]) if pd.notna(atr_14.iloc[-1]) else None
    atr_ratio = round(atr_now / atr_avg, 2) if atr_avg else None
    if atr_ratio:
        if 0.8 <= atr_ratio <= 1.5:
            score += 1; signals.append(f"ATR in normal range ({atr_ratio:.1f}x avg)")
        elif atr_ratio > 2.0:
            score -= 1; signals.append(f"ATR extended ({atr_ratio:.1f}x avg) — volatile")

    # VWAP position (approximate from daily OHLC)
    today_open    = float(df["open"].iloc[-1])
    today_close   = float(close.iloc[-1])
    today_volume  = float(volume.iloc[-1])
    typical_price = (float(high.iloc[-1]) + float(low.iloc[-1]) + today_close) / 3
    above_vwap    = today_close > typical_price

    # VWAP reclaim: opened below VWAP (open < typical), now above it — stronger signal
    opened_below_vwap = today_open < typical_price
    vwap_reclaim = above_vwap and opened_below_vwap
    vwap_signal  = "RECLAIM" if vwap_reclaim else ("ABOVE" if above_vwap else "BELOW")
    if vwap_reclaim:
        score += 2; signals.append("VWAP reclaim — opened below, now above (high conviction)")
    elif above_vwap:
        score += 1; signals.append("Price above VWAP")
    else:
        score -= 1; signals.append("Price below VWAP")

    # Gap behavior — gap up and holding vs fading
    prev_close_px = float(close.iloc[-2]) if len(close) >= 2 else today_open
    gap_pct = (today_open - prev_close_px) / prev_close_px * 100
    if gap_pct > 1.0:
        if today_close >= today_open * 0.99:
            score += 1; signals.append(f"Gap up {gap_pct:.1f}% holding — continuation")
        else:
            score -= 1; signals.append(f"Gap up {gap_pct:.1f}% fading — weakness")
    elif gap_pct < -1.0:
        score -= 1; signals.append(f"Gap down {gap_pct:.1f}%")

    # Opening range context — is price above today's open?
    if today_close > today_open:
        score += 1; signals.append("Trading above open — intraday uptrend")

    # Relative strength vs sector ETF
    sector_return = _fetch_sector_return(ticker)
    stock_return  = float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]) if len(close) >= 2 else 0
    rs_vs_sector  = None
    if sector_return is not None and abs(sector_return) > 0.001:
        rs_vs_sector = round(stock_return / abs(sector_return), 2)
        if rs_vs_sector > 1.5:
            score += 2; signals.append(f"Strong sector RS ({rs_vs_sector:.1f}x)")
        elif rs_vs_sector > 0.5:
            score += 1; signals.append(f"Positive sector RS ({rs_vs_sector:.1f}x)")
        elif rs_vs_sector < 0:
            score -= 1; signals.append(f"Negative sector RS ({rs_vs_sector:.1f}x)")

    return {
        "atr":            round(atr_avg, 2) if atr_avg else None,
        "atr_ratio":      atr_ratio,
        "above_vwap":     above_vwap,
        "vwap_signal":    vwap_signal,
        "vwap_reclaim":   vwap_reclaim,
        "gap_pct":        round(gap_pct, 2),
        "rs_vs_sector":   rs_vs_sector,
        "behavior_score": score,
        "behavior_signals": signals,
    }


def _score_ticker(ticker: str, skip_volume_surge: bool = False) -> dict | None:
    info, df = _fetch(ticker)
    if df is None or info is None:
        return None

    close  = df["close"]
    volume = df["volume"]

    avg_vol = info.get("averageVolume") or int(volume.tail(20).mean())
    cur_vol = int(volume.iloc[-1])
    cur_price = float(close.iloc[-1])

    if cur_price < MIN_PRICE or avg_vol < MIN_AVG_VOLUME:
        return None

    vol_ratio = round(cur_vol / avg_vol, 2) if avg_vol else 0
    if not skip_volume_surge and vol_ratio < MIN_VOLUME_RATIO:
        return None

    score   = 0
    signals = []

    # RSI
    rsi = ta.momentum.RSIIndicator(close, 14).rsi().iloc[-1]
    if pd.notna(rsi):
        if rsi < RSI_OVERSOLD:
            score += 2; signals.append(f"RSI oversold ({rsi:.1f})")
        elif rsi > RSI_OVERBOUGHT:
            score -= 2; signals.append(f"RSI overbought ({rsi:.1f})")

    # MACD
    macd = ta.trend.MACD(close)
    hist = macd.macd_diff().iloc[-1]
    macd_signal = None
    if pd.notna(hist):
        if hist > 0:
            score += 1; macd_signal = "BUY"; signals.append("MACD bullish crossover")
        else:
            score -= 1; macd_signal = "SELL"; signals.append("MACD bearish")

    # Bollinger Bands
    bb = ta.volatility.BollingerBands(close, 20, 2)
    bb_lower = bb.bollinger_lband().iloc[-1]
    bb_upper = bb.bollinger_hband().iloc[-1]
    bb_signal = None
    if pd.notna(bb_lower) and pd.notna(bb_upper):
        if cur_price <= bb_lower:
            score += 2; bb_signal = "LOWER"; signals.append("At lower Bollinger Band")
        elif cur_price >= bb_upper:
            score -= 1; bb_signal = "UPPER"

    # Volume — skipped at premarket (partial day vs full-day avg is meaningless at open)
    if not skip_volume_surge:
        if vol_ratio >= 2.0:
            score += 2; signals.append(f"High volume ({vol_ratio:.1f}x)")
        elif vol_ratio >= 1.5:
            score += 1; signals.append(f"Elevated volume ({vol_ratio:.1f}x)")

    # SMA context
    sma20 = close.tail(20).mean()
    sma50 = close.tail(50).mean() if len(close) >= 50 else None
    price_vs_sma20 = round((cur_price - sma20) / sma20 * 100, 2)
    price_vs_sma50 = round((cur_price - sma50) / sma50 * 100, 2) if sma50 else None

    # Momentum
    momentum_5d = round((close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100, 2) if len(close) >= 6 else 0
    if momentum_5d > 3:
        score += 1; signals.append(f"5d momentum +{momentum_5d:.1f}%")

    if abs(score) < SCORE_THRESHOLD:
        return None

    behavioral = _behavioral_score(ticker, df, info)
    total_score = score + behavioral["behavior_score"]

    if abs(total_score) < SCORE_THRESHOLD:
        return None

    return {
        "ticker":         ticker,
        "technical_score": score,
        "behavior_score":  behavioral["behavior_score"],
        "total_score":     total_score,
        "current_price":  cur_price,
        "volume_ratio":   vol_ratio,
        "avg_volume":     avg_vol,
        "rsi":            round(float(rsi), 1) if pd.notna(rsi) else None,
        "macd_signal":    macd_signal,
        "bb_signal":      bb_signal,
        "price_vs_sma20": price_vs_sma20,
        "price_vs_sma50": price_vs_sma50,
        "momentum_5d":    momentum_5d,
        "above_vwap":     behavioral["above_vwap"],
        "vwap_reclaim":   behavioral["vwap_reclaim"],
        "vwap_signal":    behavioral["vwap_signal"],
        "gap_pct":        behavioral["gap_pct"],
        "atr":            behavioral["atr"],
        "atr_ratio":      behavioral["atr_ratio"],
        "rs_vs_sector":   behavioral["rs_vs_sector"],
        "signals":        signals + behavioral["behavior_signals"],
        "sector":         SECTOR_MAP.get(ticker, "Unknown"),
    }


def run_scan(tickers: list[str], workers: int = 8, skip_volume_surge: bool = False) -> list[dict]:
    """Scan tickers in parallel, return scored candidates sorted by total_score desc."""
    candidates = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_score_ticker, t, skip_volume_surge): t for t in tickers}
        for fut in as_completed(futures):
            try:
                result = fut.result()
                if result:
                    candidates.append(result)
            except Exception as e:
                print(f"[scanner] {futures[fut]}: {e}")

    candidates.sort(key=lambda x: x["total_score"], reverse=True)
    print(f"[scanner] {len(candidates)} candidates from {len(tickers)} tickers")
    return candidates
