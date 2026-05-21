"""Tests for agents/sector_guard.py"""
from __future__ import annotations
from unittest.mock import patch
import pytest
from agents import sector_guard


def _trade(ticker: str, confidence: str = "HIGH", profit: float = 100.0) -> dict:
    return {
        "ticker":           ticker,
        "confidence":       confidence,
        "estimated_profit": profit,
        "entry_price":      100.0,
        "shares":           10,
    }


def _risk_output(trades: list[dict]) -> dict:
    return {"approved_trades": trades, "rejected_trades": []}


# ---------------------------------------------------------------------------
# _get_sector
# ---------------------------------------------------------------------------

def test_get_sector_uses_sector_map():
    from config.blue_chips import SECTOR_MAP
    if not SECTOR_MAP:
        pytest.skip("SECTOR_MAP is empty")
    ticker = next(iter(SECTOR_MAP))
    assert sector_guard._get_sector(ticker) == SECTOR_MAP[ticker]


def test_get_sector_yfinance_fallback():
    with patch("yfinance.Ticker") as mock_yf:
        mock_yf.return_value.info = {"sector": "Technology"}
        result = sector_guard._get_sector("UNKNOWN_TICKER")
    assert result == "Technology"


def test_get_sector_unknown_on_exception():
    with patch("yfinance.Ticker", side_effect=Exception("network")):
        result = sector_guard._get_sector("UNKNOWN_TICKER")
    assert result == "Unknown"


# ---------------------------------------------------------------------------
# run() — no blocking
# ---------------------------------------------------------------------------

def test_run_empty_approved():
    result = sector_guard.run({"approved_trades": []})
    assert result["approved_trades"] == []
    assert result["sector_blocked"] == []


def test_run_single_trade_passes():
    with patch("agents.sector_guard._get_sector", return_value="Technology"):
        result = sector_guard.run(_risk_output([_trade("AAPL")]))
    assert len(result["approved_trades"]) == 1
    assert result["sector_blocked"] == []


def test_run_two_same_sector_both_pass_at_cap():
    with patch("agents.sector_guard._get_sector", return_value="Technology"):
        result = sector_guard.run(_risk_output([_trade("AAPL"), _trade("MSFT")]))
    assert len(result["approved_trades"]) == 2
    assert result["sector_blocked"] == []


# ---------------------------------------------------------------------------
# run() — sector cap enforcement
# ---------------------------------------------------------------------------

def test_run_three_same_sector_drops_lowest_confidence():
    trades = [
        _trade("AAPL",  confidence="HIGH",   profit=200.0),
        _trade("MSFT",  confidence="MEDIUM", profit=150.0),
        _trade("GOOGL", confidence="LOW",    profit=100.0),
    ]
    with patch("agents.sector_guard._get_sector", return_value="Technology"):
        result = sector_guard.run(_risk_output(trades))
    kept    = [t["ticker"] for t in result["approved_trades"]]
    blocked = [b["ticker"] for b in result["sector_blocked"]]
    assert "AAPL"  in kept
    assert "MSFT"  in kept
    assert "GOOGL" in blocked
    assert len(result["approved_trades"]) == 2


def test_run_drops_lower_profit_when_same_confidence():
    trades = [
        _trade("AAPL",  confidence="HIGH", profit=300.0),
        _trade("MSFT",  confidence="HIGH", profit=200.0),
        _trade("GOOGL", confidence="HIGH", profit=100.0),
    ]
    with patch("agents.sector_guard._get_sector", return_value="Technology"):
        result = sector_guard.run(_risk_output(trades))
    kept = [t["ticker"] for t in result["approved_trades"]]
    assert "AAPL" in kept
    assert "MSFT" in kept
    assert "GOOGL" not in kept


def test_run_different_sectors_all_pass():
    trades = [
        _trade("AAPL"),
        _trade("JPM"),
        _trade("JNJ"),
    ]
    sectors = {"AAPL": "Technology", "JPM": "Financials", "JNJ": "Healthcare"}
    with patch("agents.sector_guard._get_sector", side_effect=lambda t: sectors[t]):
        result = sector_guard.run(_risk_output(trades))
    assert len(result["approved_trades"]) == 3
    assert result["sector_blocked"] == []


def test_run_unknown_sector_never_blocked():
    trades = [_trade("AAPL"), _trade("MSFT"), _trade("GOOGL")]
    with patch("agents.sector_guard._get_sector", return_value="Unknown"):
        result = sector_guard.run(_risk_output(trades))
    assert len(result["approved_trades"]) == 3
    assert result["sector_blocked"] == []


def test_run_preserves_other_risk_output_keys():
    risk_out = {
        "approved_trades": [_trade("AAPL")],
        "rejected_trades": [_trade("TSLA")],
        "some_other_key":  "value",
    }
    with patch("agents.sector_guard._get_sector", return_value="Technology"):
        result = sector_guard.run(risk_out)
    assert result["rejected_trades"] == [_trade("TSLA")]
    assert result["some_other_key"] == "value"


def test_run_blocked_entry_has_reason():
    trades = [_trade("AAPL"), _trade("MSFT"), _trade("GOOGL", confidence="LOW")]
    with patch("agents.sector_guard._get_sector", return_value="Technology"):
        result = sector_guard.run(_risk_output(trades))
    assert len(result["sector_blocked"]) == 1
    assert "reason" in result["sector_blocked"][0]
    assert result["sector_blocked"][0]["sector"] == "Technology"
