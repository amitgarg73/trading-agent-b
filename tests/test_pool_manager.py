"""Tests for core/pool_manager.py"""
import pytest
from unittest.mock import patch, MagicMock
from core.pool_manager import (
    get_pool, apply_promotions_demotions, update_trade_stats,
)
from config.blue_chips import POOL_2_SEED
from config.settings import POOL_PROMOTION_SCORE, POOL_DEMOTION_SCORE


@patch("core.pool_manager.db.select")
def test_get_pool_returns_tickers(mock_select):
    mock_select.return_value = [
        {"ticker": "AAPL", "pool": 2},
        {"ticker": "MSFT", "pool": 2},
    ]
    result = get_pool(2)
    assert "AAPL" in result
    assert "MSFT" in result
    mock_select.assert_called_once_with("b_pools", filters={"pool": 2})


@patch("core.pool_manager.db.update")
def test_promotion_pool1_to_pool2(mock_update):
    scored = [{"ticker": "BAC", "pool": 1, "rolling_7d": POOL_PROMOTION_SCORE + 1}]
    result = apply_promotions_demotions(scored)
    assert "BAC" in result["promoted"]
    mock_update.assert_called()


@patch("core.pool_manager.db.update")
def test_demotion_pool2_to_pool1(mock_update):
    # Non-seed stock should be demotable
    non_seed = "FAKE_TICKER"
    scored = [{"ticker": non_seed, "pool": 2, "rolling_7d": POOL_DEMOTION_SCORE - 1}]
    result = apply_promotions_demotions(scored)
    assert non_seed in result["demoted"]


@patch("core.pool_manager.db.update")
def test_seed_stocks_can_be_demoted(mock_update):
    # Seed stocks are no longer immune — persistent losers get demoted like any Pool 2 stock
    seed_ticker = POOL_2_SEED[0]
    scored = [{"ticker": seed_ticker, "pool": 2, "rolling_7d": POOL_DEMOTION_SCORE - 1}]
    result = apply_promotions_demotions(scored)
    assert seed_ticker in result["demoted"]


@patch("core.pool_manager.db.select")
@patch("core.pool_manager.db.update")
def test_update_trade_stats_increments(mock_update, mock_select):
    mock_select.return_value = [{"ticker": "NVDA", "trade_count": 5, "win_count": 3}]
    update_trade_stats("NVDA", win=True, pnl=150.0)
    mock_update.assert_called_once_with(
        "b_pools", {"ticker": "NVDA"},
        {"trade_count": 6, "win_count": 4}
    )


@patch("core.pool_manager.db.select")
@patch("core.pool_manager.db.update")
def test_update_trade_stats_loss(mock_update, mock_select):
    mock_select.return_value = [{"ticker": "TSLA", "trade_count": 3, "win_count": 2}]
    update_trade_stats("TSLA", win=False, pnl=-80.0)
    mock_update.assert_called_once_with(
        "b_pools", {"ticker": "TSLA"},
        {"trade_count": 4, "win_count": 2}
    )
