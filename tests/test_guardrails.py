"""Tests for agents/guardrails.py"""
import pytest
from unittest.mock import patch
from agents.guardrails import check, _validate
from config.settings import TARGET_PCT, MAX_LOSS_PER_TRADE, MIN_REWARD_RISK, POSITION_SIZE_BY_CONFIDENCE


def _make_trade(**overrides) -> dict:
    entry  = 100.00
    target = round(entry * (1 + TARGET_PCT), 2)
    stop   = round(entry * (1 - MAX_LOSS_PER_TRADE), 2)
    sz     = POSITION_SIZE_BY_CONFIDENCE["HIGH"]
    shares = int(sz / entry)
    profit = round(shares * (target - entry), 2)
    loss   = round(shares * (entry - stop), 2)
    base = {
        "ticker":           "MSFT",
        "action":           "BUY",
        "entry_price":      entry,
        "target_price":     target,
        "stop_loss":        stop,
        "position_size":    sz,
        "shares":           shares,
        "estimated_profit": profit,
        "max_loss":         loss,
        "reward_risk":      round(profit / loss, 2),
        "confidence":       "HIGH",
    }
    base.update(overrides)
    return base


@patch("agents.guardrails._traded_today", return_value=set())
@patch("agents.guardrails._get_buying_power", return_value=50_000.0)
@patch("agents.guardrails._current_price", return_value=100.00)
def test_valid_trade_passes(mock_price, mock_bp, mock_traded):
    passed, rejected = check([_make_trade()])
    assert len(passed) == 1
    assert len(rejected) == 0


@patch("agents.guardrails._traded_today", return_value=set())
@patch("agents.guardrails._get_buying_power", return_value=50_000.0)
@patch("agents.guardrails._current_price", return_value=100.00)
def test_missing_required_field_rejected(mock_price, mock_bp, mock_traded):
    trade = _make_trade()
    del trade["ticker"]
    passed, rejected = check([trade])
    assert len(passed) == 0
    assert len(rejected) == 1


@patch("agents.guardrails._traded_today", return_value=set())
@patch("agents.guardrails._get_buying_power", return_value=50_000.0)
@patch("agents.guardrails._current_price", return_value=100.00)
def test_action_not_buy_rejected(mock_price, mock_bp, mock_traded):
    passed, rejected = check([_make_trade(action="SELL")])
    assert len(passed) == 0
    assert any("buy only" in r.lower() for r in rejected)


@patch("agents.guardrails._traded_today", return_value=set())
@patch("agents.guardrails._get_buying_power", return_value=50_000.0)
@patch("agents.guardrails._current_price", return_value=100.00)
def test_target_below_entry_rejected(mock_price, mock_bp, mock_traded):
    passed, rejected = check([_make_trade(target_price=95.00)])
    assert len(passed) == 0
    assert any("target" in r.lower() for r in rejected)


@patch("agents.guardrails._traded_today", return_value=set())
@patch("agents.guardrails._get_buying_power", return_value=50_000.0)
@patch("agents.guardrails._current_price", return_value=100.00)
def test_zero_shares_rejected(mock_price, mock_bp, mock_traded):
    passed, rejected = check([_make_trade(shares=0)])
    assert len(passed) == 0


@patch("agents.guardrails._traded_today", return_value={"MSFT"})
@patch("agents.guardrails._get_buying_power", return_value=50_000.0)
@patch("agents.guardrails._current_price", return_value=100.00)
def test_duplicate_ticker_rejected(mock_price, mock_bp, mock_traded):
    passed, rejected = check([_make_trade(ticker="MSFT")])
    assert len(passed) == 0
    assert any("duplicate" in r.lower() for r in rejected)


@patch("agents.guardrails._traded_today", return_value=set())
@patch("agents.guardrails._get_buying_power", return_value=50_000.0)
@patch("agents.guardrails._current_price", return_value=None)
def test_no_live_price_rejected(mock_price, mock_bp, mock_traded):
    passed, rejected = check([_make_trade()])
    assert len(passed) == 0
    assert any("price sanity" in r.lower() for r in rejected)


@patch("agents.guardrails._traded_today", return_value=set())
@patch("agents.guardrails._get_buying_power", return_value=50_000.0)
@patch("agents.guardrails._current_price", return_value=120.00)  # 20% off entry of 100
def test_stale_price_rejected(mock_price, mock_bp, mock_traded):
    passed, rejected = check([_make_trade()])
    assert len(passed) == 0
    assert any("price sanity" in r.lower() for r in rejected)


@patch("agents.guardrails._traded_today", return_value=set())
@patch("agents.guardrails._get_buying_power", return_value=100.0)  # only $100 available
@patch("agents.guardrails._current_price", return_value=100.00)
def test_insufficient_buying_power_rejected(mock_price, mock_bp, mock_traded):
    passed, rejected = check([_make_trade(position_size=POSITION_SIZE_BY_CONFIDENCE["HIGH"])])
    assert len(passed) == 0
    assert any("capital" in r.lower() for r in rejected)


@patch("agents.guardrails._traded_today", return_value=set())
@patch("agents.guardrails._get_buying_power", return_value=50_000.0)
@patch("agents.guardrails._current_price", return_value=100.00)
def test_multiple_trades_mixed(mock_price, mock_bp, mock_traded):
    good = _make_trade(ticker="AAPL")
    bad  = _make_trade(ticker="TSLA", target_price=50.00)
    passed, rejected = check([good, bad])
    assert len(passed) == 1
    assert passed[0]["ticker"] == "AAPL"
    assert len(rejected) == 1


@patch("agents.guardrails._traded_today", return_value=set())
@patch("agents.guardrails._get_buying_power", return_value=50_000.0)
@patch("agents.guardrails._current_price", return_value=100.00)
def test_target_formula_deviation_rejected(mock_price, mock_bp, mock_traded):
    passed, rejected = check([_make_trade(target_price=115.00)])
    assert len(passed) == 0
    assert any("target" in r.lower() for r in rejected)


@patch("agents.guardrails._traded_today", return_value=set())
@patch("agents.guardrails._get_buying_power", return_value=50_000.0)
@patch("agents.guardrails._current_price", return_value=100.00)
def test_intraday_capped_trade_passes(mock_price, mock_bp, mock_traded):
    """Intraday-capped trade (1% target / 0.67% stop = 1.49 R:R) must pass with MIN_REWARD_RISK=1.4."""
    from config.settings import INTRADAY_TARGET_PCT, MAX_LOSS_PER_TRADE
    entry  = 100.00
    target = round(entry * (1 + INTRADAY_TARGET_PCT), 2)  # 1% target
    stop   = round(entry * (1 - MAX_LOSS_PER_TRADE), 2)
    shares = 35
    profit = round(shares * (target - entry), 2)
    loss   = round(shares * (entry - stop), 2)
    trade  = _make_trade(
        target_price=target,
        stop_loss=stop,
        shares=shares,
        estimated_profit=profit,
        reward_risk=round(profit / loss, 2),
    )
    passed, rejected = check([trade])
    assert len(passed) == 1, f"Intraday trade blocked: {rejected}"
    assert len(rejected) == 0


# ── ATR-based stop tests (P0) ─────────────────────────────────────────────────

@patch("agents.guardrails._traded_today", return_value=set())
@patch("agents.guardrails._get_buying_power", return_value=50_000.0)
@patch("agents.guardrails._current_price", return_value=100.00)
def test_atr_stop_accepted(mock_price, mock_bp, mock_traded):
    """Trade with atr_stop_pct field should pass stop formula check even if wider than MAX_LOSS_PER_TRADE."""
    entry     = 100.00
    stop_pct  = 0.018          # 1.8% stop — wider than 0.67% formula
    stop      = round(entry * (1 - stop_pct), 2)
    target    = round(entry * (1 + 0.04), 2)
    shares    = 10
    profit    = round(shares * (target - entry), 2)
    loss      = round(shares * (entry - stop), 2)
    trade = _make_trade(
        stop_loss=stop,
        target_price=target,
        shares=shares,
        estimated_profit=profit,
        max_loss=loss,
        reward_risk=round(profit / loss, 2),
        atr_stop_pct=stop_pct,
    )
    passed, rejected = check([trade])
    assert len(passed) == 1, f"ATR-stopped trade blocked: {rejected}"


@patch("agents.guardrails._traded_today", return_value=set())
@patch("agents.guardrails._get_buying_power", return_value=50_000.0)
@patch("agents.guardrails._current_price", return_value=100.00)
def test_atr_stop_wrong_value_rejected(mock_price, mock_bp, mock_traded):
    """atr_stop_pct present but stop_loss doesn't match — rejected."""
    entry    = 100.00
    stop_pct = 0.018
    # Deliberately wrong stop (doesn't match atr_stop_pct)
    trade = _make_trade(
        stop_loss=98.00,    # should be 98.20
        target_price=round(entry * 1.04, 2),
        atr_stop_pct=stop_pct,
    )
    passed, rejected = check([trade])
    assert len(passed) == 0
    assert any("atr" in r.lower() for r in rejected)


@patch("agents.guardrails._traded_today", return_value=set())
@patch("agents.guardrails._get_buying_power", return_value=50_000.0)
@patch("agents.guardrails._current_price", return_value=100.00)
def test_no_atr_stop_pct_uses_formula(mock_price, mock_bp, mock_traded):
    """Trade without atr_stop_pct still uses fixed-formula check."""
    entry = 100.00
    trade = _make_trade(stop_loss=96.00)   # too wide for formula (0.67%)
    passed, rejected = check([trade])
    assert len(passed) == 0
    assert any("stop" in r.lower() for r in rejected)
