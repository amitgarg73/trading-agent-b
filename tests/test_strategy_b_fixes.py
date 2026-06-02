"""
Tests for Strategy B strategy.py fixes:
  1. Merge fix: pool_filter real-time data wins over scanner data for shared keys
  2. Rolling score scale: prompt includes scale definition
  3. Current time: payload includes current_time field
  4. ATR field: Strategy B scanner output includes 'atr' dollar field
"""
import pytest
from unittest.mock import patch, MagicMock


class TestMergeOrderFix:
    """
    Pre-fix: {**c, **{k: v for k, v in m.items() if k not in c}}
             scanner (c) always wins — real-time pool_filter data lost

    Post-fix: {**c, **m}
              pool_filter (m) wins for all shared keys — real-time data preserved
    """

    def test_pool_filter_wins_for_shared_keys(self):
        """vol_ratio from pool_filter (real-time) should overwrite scanner (daily)."""
        from agents.strategy import select_trades
        # Candidate from scanner with stale daily vol_ratio
        candidate = {"ticker": "AAPL", "total_score": 7, "vol_ratio": 0.5,
                     "above_vwap": False, "current_price": 185.0}
        # Pool filter provides real-time vol_ratio — should win
        pool_context = [{"ticker": "AAPL", "vol_ratio": 2.8, "above_vwap": True,
                         "rs_vs_sector": 1.9, "atr": 2.50}]

        captured = {}

        def fake_create(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            resp = MagicMock()
            resp.content = [MagicMock(text='{"trades": [], "summary": "test", "pass": false}')]
            return resp

        with patch("agents.strategy.client") as mock_client:
            mock_client.messages.create.side_effect = fake_create
            select_trades([candidate], {}, pool_context)

        assert captured["messages"], "No messages were sent to Claude"
        user_content = captured["messages"][0]["content"]
        import json
        payload = json.loads(user_content)
        enriched = payload["candidates"]
        assert len(enriched) == 1
        # pool_filter's real-time vol_ratio=2.8 must win over scanner's 0.5
        assert enriched[0]["vol_ratio"] == 2.8, (
            f"Expected pool_filter vol_ratio=2.8, got {enriched[0]['vol_ratio']}. "
            "Merge bug not fixed — scanner is still winning."
        )
        # above_vwap should also be the pool_filter value
        assert enriched[0]["above_vwap"] is True

    def test_ticker_preserved_after_merge(self):
        """Ticker must be preserved correctly in the merged candidate."""
        from agents.strategy import select_trades
        candidate = {"ticker": "MSFT", "total_score": 6, "current_price": 420.0}
        pool_context = [{"ticker": "MSFT", "vol_ratio": 1.8, "atr": 3.20}]

        captured = {}

        def fake_create(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            resp = MagicMock()
            resp.content = [MagicMock(text='{"trades": [], "summary": "ok", "pass": false}')]
            return resp

        with patch("agents.strategy.client") as mock_client:
            mock_client.messages.create.side_effect = fake_create
            select_trades([candidate], {}, pool_context)

        import json
        payload = json.loads(captured["messages"][0]["content"])
        assert payload["candidates"][0]["ticker"] == "MSFT"

    def test_scanner_only_fields_preserved(self):
        """Fields that exist only in scanner (no pool_filter key) must be retained."""
        from agents.strategy import select_trades
        candidate = {"ticker": "GOOGL", "total_score": 5, "rsi": 42.0, "current_price": 180.0}
        pool_context = [{"ticker": "GOOGL", "vol_ratio": 1.5, "atr": 2.80}]

        captured = {}

        def fake_create(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            resp = MagicMock()
            resp.content = [MagicMock(text='{"trades": [], "summary": "ok", "pass": false}')]
            return resp

        with patch("agents.strategy.client") as mock_client:
            mock_client.messages.create.side_effect = fake_create
            select_trades([candidate], {}, pool_context)

        import json
        payload = json.loads(captured["messages"][0]["content"])
        assert payload["candidates"][0]["rsi"] == 42.0  # scanner-only field preserved

    def test_no_pool_filter_uses_scanner_unchanged(self):
        """With no pool3_context, candidates pass through unchanged."""
        from agents.strategy import select_trades

        candidate = {"ticker": "NVDA", "total_score": 8, "vol_ratio": 2.0, "current_price": 900.0}

        captured = {}

        def fake_create(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            resp = MagicMock()
            resp.content = [MagicMock(text='{"trades": [], "summary": "ok", "pass": false}')]
            return resp

        with patch("agents.strategy.client") as mock_client:
            mock_client.messages.create.side_effect = fake_create
            select_trades([candidate], {}, [])  # empty pool_context

        import json
        payload = json.loads(captured["messages"][0]["content"])
        assert payload["candidates"][0]["vol_ratio"] == 2.0


class TestPayloadCurrentTime:

    def test_payload_includes_current_time(self):
        """select_trades() payload must include current_time in HH:MM ET format."""
        from agents.strategy import select_trades

        captured = {}

        def fake_create(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            resp = MagicMock()
            resp.content = [MagicMock(text='{"trades": [], "summary": "ok", "pass": false}')]
            return resp

        candidate = {"ticker": "AAPL", "total_score": 7, "current_price": 185.0, "atr": 2.50}

        with patch("agents.strategy.client") as mock_client:
            mock_client.messages.create.side_effect = fake_create
            select_trades([candidate], {}, [])

        import json
        payload = json.loads(captured["messages"][0]["content"])
        assert "current_time" in payload, "payload must include 'current_time'"
        assert "ET" in payload["current_time"], "current_time must include 'ET' timezone label"

    def test_payload_includes_date(self):
        """payload must include date in YYYY-MM-DD format."""
        from agents.strategy import select_trades

        captured = {}

        def fake_create(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            resp = MagicMock()
            resp.content = [MagicMock(text='{"trades": [], "summary": "ok", "pass": false}')]
            return resp

        candidate = {"ticker": "AAPL", "total_score": 7, "current_price": 185.0}

        with patch("agents.strategy.client") as mock_client:
            mock_client.messages.create.side_effect = fake_create
            select_trades([candidate], {}, [])

        import json
        payload = json.loads(captured["messages"][0]["content"])
        assert "date" in payload
        from datetime import date
        # Date must be parseable
        d = date.fromisoformat(payload["date"])
        assert d is not None


class TestStrategyBScannerATR:

    def test_scanner_includes_atr_dollar(self):
        """Strategy B scanner._behavioral_score() must return 'atr' (dollar) field."""
        import pandas as pd
        import numpy as np
        from datetime import date, timedelta
        from scanner.scanner import _behavioral_score

        n = 40
        dates = pd.bdate_range(end=date.today() - timedelta(days=1), periods=n)
        close = np.linspace(100, 105, n)
        high  = close + 1.5
        low   = close - 1.5
        volume = np.full(n, 10_000_000)
        df = pd.DataFrame({"close": close, "high": high, "low": low,
                           "open": close - 0.5, "volume": volume},
                          index=pd.DatetimeIndex(dates))

        result = _behavioral_score("AAPL", df, {})
        assert "atr" in result, "_behavioral_score must include 'atr' (dollar ATR) field"
        # atr should be positive (avg daily range ≈ 3.0 in this synthetic data)
        assert result["atr"] is None or result["atr"] > 0

    def test_scanner_output_includes_atr(self):
        """Full _score_ticker() output must include 'atr' key."""
        from scanner.scanner import _score_ticker
        import pandas as pd
        import numpy as np
        from datetime import date, timedelta

        n = 60
        dates = pd.bdate_range(end=date.today() - timedelta(days=1), periods=n)
        close  = np.linspace(150, 155, n)
        high   = close + 2.0
        low    = close - 2.0
        volume = np.full(n, 20_000_000)
        df = pd.DataFrame({"close": close, "high": high, "low": low,
                           "open": close - 0.5, "volume": volume},
                          index=pd.DatetimeIndex(dates))

        info = {"averageVolume": 20_000_000, "longName": "Apple Inc", "sector": "Technology"}

        with (
            patch("scanner.scanner._fetch", return_value=(info, df)),
            patch("scanner.scanner._fetch_sector_return", return_value=0.005),
        ):
            result = _score_ticker("AAPL")

        if result is not None:
            assert "atr" in result, "_score_ticker output must include 'atr' field"


# ── SPY gate and daily entry cap (intraday) ───────────────────────────────────

from datetime import datetime as real_dt, date as real_date
from unittest.mock import MagicMock, patch
from config.settings import MAX_DAILY_ENTRIES, INTRADAY_SCAN_UTC_START


def _run_intraday_alpaca(spy_pct: float, daily_opened: int = 0):
    """
    Run _maybe_run_intraday_scan(broker="alpaca") with SPY pct and daily opened count mocked.
    Returns mock_momentum so callers can assert whether the scan pipeline ran.
    """
    fake_now = real_dt(2026, 5, 27, INTRADAY_SCAN_UTC_START, 30, 0)
    today_str = "2026-05-27"

    # Build b_positions rows for daily_opened simulation
    daily_rows = [{"opened_at": f"{today_str}T{14+i:02d}:00:00"} for i in range(daily_opened)]

    spy_sig = {"SPY": {"today_pct_change": spy_pct}}
    mock_momentum = MagicMock(return_value=[])

    def db_select(table, **kw):
        f    = kw.get("filters", {})
        fgte = kw.get("filters_gte", {})
        if table == "b_scan_results":
            return []
        if f.get("status") == "OPEN":
            return []
        if f.get("status") == "CLOSED" and not fgte:
            return [{"realized_pnl": 100.0, "closed_at": f"{today_str}T14:00:00", "close_reason": "TARGET"}]
        if fgte:
            return daily_rows
        return []

    with patch("orchestrator.datetime") as mock_dt, \
         patch("orchestrator.date") as mock_date, \
         patch("core.db.select",  side_effect=db_select), \
         patch("core.db.insert",  return_value={"id": "x"}), \
         patch("orchestrator.open_positions", return_value=[]), \
         patch("agents.alpaca_broker.get_intraday_signals", return_value=spy_sig), \
         patch("scanner.intraday_momentum.scan", mock_momentum), \
         patch("scanner.pool_filter.get_pool3_tickers", return_value=["AAPL", "MSFT"]), \
         patch("orchestrator.MIN_SPY_MOVE_PCT", 0.003):
        mock_dt.utcnow.return_value = fake_now
        mock_dt.fromisoformat = real_dt.fromisoformat
        mock_date.today.return_value = real_date(2026, 5, 27)
        from orchestrator import _maybe_run_intraday_scan
        _maybe_run_intraday_scan(broker="alpaca")

    return mock_momentum


class TestStrategyBSPYGate:
    """SPY gate — TEMPORARILY DISABLED. All cases pass through to momentum scan."""

    def test_spy_gate_blocks_negative_spy(self):
        """Gate disabled: SPY -0.5% no longer blocks."""
        mock_momentum = _run_intraday_alpaca(spy_pct=-0.5)
        mock_momentum.assert_called_once()

    def test_spy_gate_blocks_flat_market(self):
        """Gate disabled: SPY +0.1% no longer blocks."""
        mock_momentum = _run_intraday_alpaca(spy_pct=0.1)
        mock_momentum.assert_called_once()

    def test_spy_gate_passes_above_threshold(self):
        """Gate disabled: SPY +0.5% passes through as before."""
        mock_momentum = _run_intraday_alpaca(spy_pct=0.5)
        mock_momentum.assert_called_once()


class TestStrategyBDailyEntryCap:
    """Daily entry cap must prevent intraday scan when MAX_DAILY_ENTRIES already opened."""

    def test_cap_blocks_when_limit_reached(self):
        """Already opened MAX_DAILY_ENTRIES positions today → scan skipped."""
        mock_momentum = _run_intraday_alpaca(spy_pct=0.5, daily_opened=MAX_DAILY_ENTRIES)
        mock_momentum.assert_not_called()

    def test_cap_allows_when_one_slot_left(self):
        """One slot remaining under cap → scan proceeds."""
        mock_momentum = _run_intraday_alpaca(spy_pct=0.5, daily_opened=MAX_DAILY_ENTRIES - 1)
        mock_momentum.assert_called_once()
