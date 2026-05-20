"""Tests for agents/guardrails.py"""
import pytest
from agents.guardrails import check, _validate
from config.settings import TARGET_PCT, MAX_LOSS_PER_TRADE, MIN_REWARD_RISK


def _make_trade(**overrides) -> dict:
    entry  = 100.00
    target = round(entry * (1 + TARGET_PCT), 2)
    stop   = round(entry * (1 - MAX_LOSS_PER_TRADE), 2)
    shares = 70
    profit = round(shares * (target - entry), 2)
    loss   = round(shares * (entry - stop), 2)
    base = {
        "ticker":          "MSFT",
        "action":          "BUY",
        "entry_price":     entry,
        "target_price":    target,
        "stop_loss":       stop,
        "position_size":   7000,
        "shares":          shares,
        "estimated_profit": profit,
        "max_loss":        loss,
        "reward_risk":     round(profit / loss, 2),
        "confidence":      "HIGH",
    }
    base.update(overrides)
    return base


def test_valid_trade_passes():
    trade = _make_trade()
    passed, rejected = check([trade])
    assert len(passed) == 1
    assert len(rejected) == 0


def test_missing_required_field_rejected():
    trade = _make_trade()
    del trade["ticker"]
    passed, rejected = check([trade])
    assert len(passed) == 0
    assert len(rejected) == 1


def test_target_below_entry_rejected():
    trade = _make_trade(target_price=95.00)
    passed, rejected = check([trade])
    assert len(passed) == 0
    assert "target" in rejected[0].lower()


def test_stop_above_entry_rejected():
    trade = _make_trade(stop_loss=110.00)
    passed, rejected = check([trade])
    assert len(passed) == 0


def test_zero_shares_rejected():
    trade = _make_trade(shares=0)
    passed, rejected = check([trade])
    assert len(passed) == 0


def test_multiple_trades_mixed():
    good  = _make_trade(ticker="AAPL")
    bad   = _make_trade(ticker="TSLA", target_price=50.00)
    passed, rejected = check([good, bad])
    assert len(passed) == 1
    assert passed[0]["ticker"] == "AAPL"
    assert len(rejected) == 1


def test_target_formula_deviation_rejected():
    # Target deviates by 10% from formula — should reject
    trade = _make_trade(target_price=115.00)  # way off from 102.00
    passed, rejected = check([trade])
    assert len(passed) == 0
    assert "target" in rejected[0].lower()
