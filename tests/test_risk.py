"""Tests for agents/risk.py"""
import pytest
from unittest.mock import patch, MagicMock
from agents.risk import validate
from config.settings import TARGET_PCT, MAX_LOSS_PER_TRADE, TOTAL_CAPITAL, MAX_POSITION_PCT


def _trade(ticker="AAPL", pool=2, ps=3000, rr=3.0) -> dict:
    entry  = 185.00
    target = round(entry * (1 + TARGET_PCT), 2)
    stop   = round(entry * (1 - MAX_LOSS_PER_TRADE), 2)
    shares = int(ps / entry)
    profit = round(shares * (target - entry), 2)
    loss   = round(shares * (entry - stop), 2)
    return {
        "ticker": ticker, "pool": pool, "action": "BUY",
        "entry_price": entry, "target_price": target, "stop_loss": stop,
        "position_size": ps, "shares": shares,
        "estimated_profit": profit, "reward_risk": rr, "confidence": "HIGH",
    }


@patch("agents.risk.db.select", return_value=[])
@patch("agents.risk._today_realized_pnl", return_value=0.0)
def test_valid_trade_approved(mock_pnl, mock_select):
    approved, rejected = validate([_trade()])
    assert len(approved) == 1
    assert len(rejected) == 0


@patch("agents.risk.db.select", return_value=[])
@patch("agents.risk._today_realized_pnl", return_value=-600.0)
def test_daily_loss_limit_blocks_all(mock_pnl, mock_select):
    approved, rejected = validate([_trade()])
    assert len(approved) == 0
    assert any("loss limit" in r.lower() for r in rejected)


@patch("agents.risk._today_realized_pnl", return_value=0.0)
def test_duplicate_ticker_rejected(mock_pnl):
    open_pos = [{"ticker": "AAPL", "pool": 2, "status": "OPEN",
                 "entry_price": 185, "shares": 10}]
    with patch("agents.risk.db.select", return_value=open_pos):
        approved, rejected = validate([_trade("AAPL")])
    assert len(approved) == 0
    assert any("position" in r.lower() for r in rejected)


@patch("agents.risk.db.select", return_value=[])
@patch("agents.risk._today_realized_pnl", return_value=0.0)
def test_sector_limit_enforced(mock_pnl, mock_select):
    # MAX_PER_SECTOR = 2 for Strategy B — third tech stock should be rejected
    trades = [_trade("AAPL"), _trade("MSFT"), _trade("NVDA")]
    approved, rejected = validate(trades)
    # First 2 tech stocks approved, third rejected
    tech_approved = [t for t in approved if t["ticker"] in ("AAPL", "MSFT", "NVDA")]
    assert len(tech_approved) <= 2


@patch("agents.risk.db.select", return_value=[])
@patch("agents.risk._today_realized_pnl", return_value=0.0)
def test_position_size_too_large_rejected(mock_pnl, mock_select):
    oversized = int(TOTAL_CAPITAL * MAX_POSITION_PCT * 2)  # 14% of $50K = $7,000 — over limit
    approved, rejected = validate([_trade(ps=oversized)])
    assert len(approved) == 0
    assert any("size" in r.lower() for r in rejected)


@patch("agents.risk.db.select", return_value=[])
@patch("agents.risk._today_realized_pnl", return_value=0.0)
def test_rr_below_minimum_rejected(mock_pnl, mock_select):
    approved, rejected = validate([_trade(rr=1.2)])  # below MIN_REWARD_RISK=1.4
    assert len(approved) == 0
    assert any("r:r" in r.lower() for r in rejected)
