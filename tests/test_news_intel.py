"""Tests for agents/news_intel.py"""
from __future__ import annotations
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
import pytest
from agents import news_intel


def _candidate(ticker: str) -> dict:
    return {"ticker": ticker, "entry_price": 100.0, "score": 5}


# ---------------------------------------------------------------------------
# _get_earnings_date
# ---------------------------------------------------------------------------

def test_get_earnings_date_dict_format():
    from datetime import datetime
    mock_cal = {"Earnings Date": [datetime(2026, 6, 1, 0, 0)]}
    with patch("agents.news_intel.yf.Ticker") as mock_yf:
        mock_yf.return_value.calendar = mock_cal
        result = news_intel._get_earnings_date("AAPL")
    assert result == date(2026, 6, 1)


def test_get_earnings_date_none_when_calendar_none():
    with patch("agents.news_intel.yf.Ticker") as mock_yf:
        mock_yf.return_value.calendar = None
        result = news_intel._get_earnings_date("AAPL")
    assert result is None


def test_get_earnings_date_none_on_exception():
    with patch("agents.news_intel.yf.Ticker", side_effect=Exception("network error")):
        result = news_intel._get_earnings_date("AAPL")
    assert result is None


def test_get_earnings_date_dict_single_value():
    from datetime import datetime
    dt = datetime(2026, 6, 1, 0, 0)
    mock_cal = {"Earnings Date": dt}
    with patch("agents.news_intel.yf.Ticker") as mock_yf:
        mock_yf.return_value.calendar = mock_cal
        result = news_intel._get_earnings_date("AAPL")
    assert result == date(2026, 6, 1)


# ---------------------------------------------------------------------------
# _get_news
# ---------------------------------------------------------------------------

def test_get_news_returns_titles():
    mock_news = [
        {"title": "Apple hits record high"},
        {"title": "iPhone sales surge"},
        {"title": "Apple beats estimates"},
        {"title": "Extra headline"},
    ]
    with patch("agents.news_intel.yf.Ticker") as mock_yf:
        mock_yf.return_value.news = mock_news
        result = news_intel._get_news("AAPL", max_headlines=3)
    assert result == ["Apple hits record high", "iPhone sales surge", "Apple beats estimates"]


def test_get_news_falls_back_to_headline_key():
    mock_news = [{"headline": "MSFT cloud revenue up 20%"}]
    with patch("agents.news_intel.yf.Ticker") as mock_yf:
        mock_yf.return_value.news = mock_news
        result = news_intel._get_news("MSFT")
    assert result == ["MSFT cloud revenue up 20%"]


def test_get_news_empty_on_exception():
    with patch("agents.news_intel.yf.Ticker", side_effect=Exception("timeout")):
        result = news_intel._get_news("AAPL")
    assert result == []


def test_get_news_empty_when_no_news():
    with patch("agents.news_intel.yf.Ticker") as mock_yf:
        mock_yf.return_value.news = []
        result = news_intel._get_news("AAPL")
    assert result == []


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

def test_run_empty_candidates():
    result = news_intel.run([])
    assert result["filtered_candidates"] == []
    assert result["blackout_tickers"] == []
    assert result["news_context"] == ""


def test_run_blocks_earnings_today():
    today = date.today()
    with patch("agents.news_intel._get_earnings_date", return_value=today), \
         patch("agents.news_intel._get_news", return_value=[]):
        result = news_intel.run([_candidate("AAPL")])
    assert result["filtered_candidates"] == []
    assert len(result["blackout_tickers"]) == 1
    assert result["blackout_tickers"][0]["ticker"] == "AAPL"


def test_run_blocks_earnings_tomorrow():
    tomorrow = date.today() + timedelta(days=1)
    with patch("agents.news_intel._get_earnings_date", return_value=tomorrow), \
         patch("agents.news_intel._get_news", return_value=[]):
        result = news_intel.run([_candidate("MSFT")])
    assert result["filtered_candidates"] == []
    assert result["blackout_tickers"][0]["ticker"] == "MSFT"


def test_run_passes_non_earnings_candidates():
    with patch("agents.news_intel._get_earnings_date", return_value=None), \
         patch("agents.news_intel._get_news", return_value=[]):
        result = news_intel.run([_candidate("GOOGL")])
    assert len(result["filtered_candidates"]) == 1
    assert result["blackout_tickers"] == []


def test_run_earnings_far_future_not_blocked():
    far_future = date.today() + timedelta(days=30)
    with patch("agents.news_intel._get_earnings_date", return_value=far_future), \
         patch("agents.news_intel._get_news", return_value=[]):
        result = news_intel.run([_candidate("AAPL")])
    assert len(result["filtered_candidates"]) == 1


def test_run_mixed_candidates():
    today = date.today()
    candidates = [_candidate("AAPL"), _candidate("MSFT"), _candidate("GOOGL")]

    def fake_earnings(ticker):
        return today if ticker == "MSFT" else None

    with patch("agents.news_intel._get_earnings_date", side_effect=fake_earnings), \
         patch("agents.news_intel._get_news", return_value=[]):
        result = news_intel.run(candidates)
    tickers = [c["ticker"] for c in result["filtered_candidates"]]
    assert "MSFT" not in tickers
    assert "AAPL" in tickers
    assert "GOOGL" in tickers
    assert len(result["blackout_tickers"]) == 1


def test_run_news_context_built():
    with patch("agents.news_intel._get_earnings_date", return_value=None), \
         patch("agents.news_intel._get_news", return_value=["AAPL up 3%"]):
        result = news_intel.run([_candidate("AAPL")])
    assert "AAPL" in result["news_context"]
    assert "AAPL up 3%" in result["news_context"]


def test_run_news_context_empty_when_no_headlines():
    with patch("agents.news_intel._get_earnings_date", return_value=None), \
         patch("agents.news_intel._get_news", return_value=[]):
        result = news_intel.run([_candidate("AAPL")])
    assert result["news_context"] == ""
