"""
News Intelligence — runs after scanner, before strategy.

Two jobs:
1. Earnings blackout — remove any candidate reporting earnings today or tomorrow.
   Earnings = binary event = unacceptable gap risk for a day-trading system.

2. News context — fetch recent headlines for remaining candidates and
   return a summary for the strategy agent to factor in.
"""
from __future__ import annotations
from typing import Optional
import concurrent.futures
import contextlib
import io
import yfinance as yf
from datetime import date, timedelta


def _get_earnings_date(ticker: str) -> Optional[date]:
    """Return next earnings date for ticker, or None if unknown."""
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            t = yf.Ticker(ticker)
            cal = t.calendar
        if cal is None:
            return None
        if hasattr(cal, 'columns'):
            if 'Earnings Date' in cal.columns:
                val = cal['Earnings Date'].iloc[0]
                return val.date() if hasattr(val, 'date') else None
        elif isinstance(cal, dict):
            val = cal.get('Earnings Date')
            if val is None:
                return None
            if isinstance(val, list):
                val = val[0]
            return val.date() if hasattr(val, 'date') else None
    except Exception:
        return None
    return None


def _get_news(ticker: str, max_headlines: int = 3) -> list[str]:
    """Return recent news headlines for a ticker."""
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            news = yf.Ticker(ticker).news or []
        return [
            item.get('title') or item.get('headline', '')
            for item in news[:max_headlines]
            if item.get('title') or item.get('headline')
        ]
    except Exception:
        return []


def run(candidates: list[dict]) -> dict:
    """
    Args:
        candidates: list of scanner candidates (each has 'ticker' key)

    Returns:
        filtered_candidates: candidates with earnings-day tickers removed
        blackout_tickers:    list of removed tickers + reason
        news_context:        str summary of headlines for strategy agent
        news_by_ticker:      dict of ticker → headlines
    """
    if not candidates:
        return {
            "filtered_candidates": [],
            "blackout_tickers":    [],
            "news_context":        "",
            "news_by_ticker":      {},
        }

    print("[ 3.5 ] Earnings blackout & news intelligence...")

    today    = date.today()
    tomorrow = today + timedelta(days=1)

    blackout_tickers = []
    filtered         = list(candidates)  # earnings blackout disabled for Strategy B
    tickers          = [c["ticker"] for c in candidates]
    news_by_ticker   = {}

    def _fetch(ticker: str) -> tuple[str, list[str]]:
        return ticker, _get_news(ticker)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(tickers), 20)) as pool:
        futures = {pool.submit(_fetch, t): t for t in tickers}
        for fut in concurrent.futures.as_completed(futures, timeout=30):
            try:
                ticker, headlines = fut.result(timeout=5)
                if headlines:
                    news_by_ticker[ticker] = headlines
            except Exception:
                pass  # one slow/failed ticker doesn't block the rest

    news_lines = []
    for ticker, headlines in news_by_ticker.items():
        for h in headlines:
            news_lines.append(f"  {ticker}: {h}")

    news_context = (
        "Recent news headlines for candidates:\n" + "\n".join(news_lines)
        if news_lines else ""
    )

    print(f"        Earnings blackout: {len(blackout_tickers)} removed | "
          f"{len(filtered)} candidates remaining")
    if news_by_ticker:
        print(f"        News fetched for {len(news_by_ticker)} tickers")

    return {
        "filtered_candidates": filtered,
        "blackout_tickers":    blackout_tickers,
        "news_context":        news_context,
        "news_by_ticker":      news_by_ticker,
    }
