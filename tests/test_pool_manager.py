"""Tests for core/pool_manager.py"""
import pytest
from unittest.mock import patch, MagicMock, call
from core.pool_manager import (
    get_pool, apply_promotions_demotions, update_trade_stats, seed_pools_if_empty,
)
from config.blue_chips import POOL_2_SEED
from config.settings import POOL_PROMOTION_SCORE, POOL_DEMOTION_SCORE


# ── seed_pools_if_empty ──────────────────────────────────────────────────────

@patch("core.pool_manager.db.insert")
@patch("core.pool_manager.db.select", return_value=[])
def test_seed_inserts_all_seed_stocks_on_first_run(mock_select, mock_insert):
    seed_pools_if_empty()
    inserted_tickers = {c.args[1]["ticker"] for c in mock_insert.call_args_list}
    for t in POOL_2_SEED:
        assert t in inserted_tickers, f"{t} not inserted on first run"


@patch("core.pool_manager.db.update")
@patch("core.pool_manager.db.insert")
@patch("core.pool_manager.db.select")
def test_seed_sync_adds_missing_pool2_stocks(mock_select, mock_insert, mock_update):
    """If table is non-empty but a POOL_2_SEED stock is absent, it gets added."""
    existing = [{"ticker": t, "pool": 2} for t in POOL_2_SEED[:10]]
    mock_select.return_value = existing
    seed_pools_if_empty()
    inserted_tickers = {c.args[1]["ticker"] for c in mock_insert.call_args_list}
    for t in POOL_2_SEED[10:]:
        assert t in inserted_tickers, f"{t} should have been added by sync"


@patch("core.pool_manager.db.update")
@patch("core.pool_manager.db.insert")
@patch("core.pool_manager.db.select")
def test_seed_sync_moves_seed_stock_from_pool1_to_pool2(mock_select, mock_insert, mock_update):
    """A POOL_2_SEED stock wrongly in Pool 1 gets moved to Pool 2."""
    existing = [{"ticker": t, "pool": 2} for t in POOL_2_SEED]
    existing[0] = {"ticker": POOL_2_SEED[0], "pool": 1}  # first seed stock in wrong pool
    mock_select.return_value = existing
    seed_pools_if_empty()
    updated = [c for c in mock_update.call_args_list if c.args[1] == {"ticker": POOL_2_SEED[0]}]
    assert updated, f"{POOL_2_SEED[0]} should have been moved to pool 2"
    assert updated[0].args[2]["pool"] == 2


@patch("core.pool_manager.db.update")
@patch("core.pool_manager.db.insert")
@patch("core.pool_manager.db.select")
def test_seed_sync_noop_when_fully_synced(mock_select, mock_insert, mock_update):
    """No inserts or updates when all seed stocks are already in Pool 2."""
    existing = [{"ticker": t, "pool": 2} for t in POOL_2_SEED]
    mock_select.return_value = existing
    seed_pools_if_empty()
    mock_insert.assert_not_called()
    mock_update.assert_not_called()


# ── get_pool ─────────────────────────────────────────────────────────────────

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
