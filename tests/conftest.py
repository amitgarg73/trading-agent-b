"""
Shared fixtures for Strategy B tests.
"""
import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, patch
from config.settings import POSITION_SIZE_BY_CONFIDENCE, TOTAL_CAPITAL


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """30-day OHLCV dataframe simulating a trending stock."""
    n = 30
    close = [100 + i * 0.5 + np.random.normal(0, 0.3) for i in range(n)]
    df = pd.DataFrame({
        "open":   [c - 0.2 for c in close],
        "high":   [c + 0.8 for c in close],
        "low":    [c - 0.6 for c in close],
        "close":  close,
        "volume": [2_000_000 + np.random.randint(-500_000, 500_000) for _ in range(n)],
    })
    return df


@pytest.fixture
def bullish_candidate() -> dict:
    """A candidate that should pass all checks."""
    return {
        "ticker":          "AAPL",
        "pool":            2,
        "total_score":     8,
        "technical_score": 6,
        "behavior_score":  2,
        "current_price":   185.00,
        "volume_ratio":    2.1,
        "avg_volume":      60_000_000,
        "rsi":             42.0,
        "macd_signal":     "BUY",
        "bb_signal":       "LOWER",
        "price_vs_sma20":  -1.2,
        "price_vs_sma50":  2.1,
        "momentum_5d":     3.5,
        "above_vwap":      True,
        "atr_ratio":       1.1,
        "rs_vs_sector":    1.8,
        "sector":          "Technology",
        "rolling_score":   7.2,
        "signals":         ["RSI oversold (42.0)", "MACD bullish", "Strong sector RS"],
    }


@pytest.fixture
def valid_trade() -> dict:
    """A trade dict that passes risk and guardrails."""
    entry  = 185.00
    target = round(entry * 1.02, 2)
    stop   = round(entry * (1 - 0.0067), 2)
    sz     = POSITION_SIZE_BY_CONFIDENCE["HIGH"]
    shares = int(sz / entry)
    profit = round(shares * (target - entry), 2)
    loss   = round(shares * (entry - stop), 2)
    return {
        "ticker":          "AAPL",
        "pool":            2,
        "action":          "BUY",
        "entry_price":     entry,
        "target_price":    target,
        "stop_loss":       stop,
        "position_size":   sz,
        "shares":          shares,
        "estimated_profit": profit,
        "max_loss":        loss,
        "reward_risk":     round(profit / loss, 2),
        "confidence":      "HIGH",
        "reasoning":       "Test trade",
        "strategy":        "b",
    }


@pytest.fixture
def mock_db(monkeypatch):
    """Mock core.db to avoid real Supabase calls in tests."""
    mock = MagicMock()
    mock.select.return_value = []
    mock.insert.return_value = {"id": "test-uuid"}
    mock.upsert.return_value = {"id": "test-uuid"}
    mock.update.return_value = []
    monkeypatch.setattr("core.db.select", mock.select)
    monkeypatch.setattr("core.db.insert", mock.insert)
    monkeypatch.setattr("core.db.upsert", mock.upsert)
    monkeypatch.setattr("core.db.update", mock.update)
    return mock
