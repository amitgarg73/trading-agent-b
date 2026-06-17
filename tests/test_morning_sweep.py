"""
Tests for RF-4 + RFN-1 + RFN-3 + RFN-4 + RFN-5 in Strategy B.

_sweep_and_verify():
  - Only acts on positions in our DB as OPEN (skips Strategy A's positions)
  - Returns False + alert + halt flag after 2 failed attempts

EOD alert (RFN-3):
  - Fires when ANY position fails to close at EOD, not just when ALL fail

Reconciliation (RFN-4 + RFN-5):
  - Order fetch uses today_start date filter and limit=500
  - Enum comparisons use getattr(..., "value", ...) not str()
"""
import pytest
from unittest.mock import patch, MagicMock, call, ANY


OUR_POS = [{"ticker": "AAPL"}]


class TestMorningSweepB:

    def test_no_overnight_positions(self):
        """Empty Alpaca account → True immediately, no close calls."""
        with patch("agents.alpaca_broker.get_open_tickers", return_value=set()), \
             patch("orchestrator.close_all_positions") as mock_close, \
             patch("agents.alpaca_broker._get", return_value=MagicMock()), \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            result = _sweep_and_verify()

        assert result is True
        mock_close.assert_not_called()

    def test_only_other_strategy_positions(self):
        """Alpaca has tickers but none in our DB — Strategy A's positions, skip."""
        with patch("agents.alpaca_broker.get_open_tickers", return_value={"MSFT"}), \
             patch("orchestrator.open_positions", return_value=[]), \
             patch("orchestrator.close_all_positions") as mock_close, \
             patch("agents.alpaca_broker._get", return_value=MagicMock()), \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            result = _sweep_and_verify()

        assert result is True
        mock_close.assert_not_called()

    def test_clears_on_first_attempt(self):
        """Our position cleared after first close → True, no alert."""
        side_effects = [{"AAPL"}, set()]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("orchestrator.open_positions", return_value=OUR_POS), \
             patch("orchestrator.close_all_positions") as mock_close, \
             patch("agents.alpaca_broker._get", return_value=MagicMock()), \
             patch("orchestrator.send_alert") as mock_alert, \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            result = _sweep_and_verify()

        assert result is True
        mock_close.assert_called_once()
        mock_alert.assert_not_called()

    def test_clears_on_second_attempt(self):
        """Still dirty after first close, clear after second → True, no alert."""
        side_effects = [{"AAPL"}, {"AAPL"}, set()]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("orchestrator.open_positions", return_value=OUR_POS), \
             patch("orchestrator.close_all_positions") as mock_close, \
             patch("agents.alpaca_broker._get", return_value=MagicMock()), \
             patch("orchestrator.send_alert") as mock_alert, \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            result = _sweep_and_verify()

        assert result is True
        assert mock_close.call_count == 2
        mock_alert.assert_not_called()

    def test_fails_after_two_attempts_returns_false(self):
        """Dirty after both attempts → returns False."""
        side_effects = [{"AAPL"}, {"AAPL"}, {"AAPL"}]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("orchestrator.open_positions", return_value=OUR_POS), \
             patch("orchestrator.close_all_positions"), \
             patch("agents.alpaca_broker._get", return_value=MagicMock()), \
             patch("core.ledger.log"), \
             patch("core.db.insert"), \
             patch("orchestrator.send_alert"), \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            result = _sweep_and_verify()

        assert result is False

    def test_fails_after_two_attempts_sends_alert_with_details(self):
        """Alert includes ticker, Alpaca URL, and Strategy B restart URL."""
        side_effects = [{"AAPL"}, {"AAPL"}, {"AAPL"}]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("orchestrator.open_positions", return_value=OUR_POS), \
             patch("orchestrator.close_all_positions"), \
             patch("agents.alpaca_broker._get", return_value=MagicMock()), \
             patch("core.ledger.log"), \
             patch("core.db.insert"), \
             patch("orchestrator.send_alert") as mock_alert, \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            _sweep_and_verify()

        mock_alert.assert_called_once()
        subject, body = mock_alert.call_args[0]
        assert "HALTED" in subject
        assert "AAPL" in body
        assert "app.alpaca.markets" in body
        assert "restart.yml" in body
        assert "trading-agent-b" in body
        assert "STEP 1" in body
        assert "STEP 2" in body

    def test_fails_after_two_attempts_sets_halt_flag(self):
        """Sweep failure inserts scan_type='halt_flag' into b_scan_results."""
        side_effects = [{"MSFT"}, {"MSFT"}, {"MSFT"}]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("orchestrator.open_positions", return_value=[{"ticker": "MSFT"}]), \
             patch("orchestrator.close_all_positions"), \
             patch("agents.alpaca_broker._get", return_value=MagicMock()), \
             patch("core.ledger.log"), \
             patch("core.db.insert") as mock_insert, \
             patch("orchestrator.send_alert"), \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            _sweep_and_verify()

        halt_call = next(
            (c for c in mock_insert.call_args_list
             if c[0][0] == "b_scan_results" and c[0][1].get("scan_type") == "halt_flag"),
            None,
        )
        assert halt_call is not None, "Expected halt_flag insert into b_scan_results"

    def test_fails_after_two_attempts_logs_to_ledger(self):
        """Sweep failure writes event_type='sweep_failed' to local ledger."""
        side_effects = [{"NVDA"}, {"NVDA"}, {"NVDA"}]
        with patch("agents.alpaca_broker.get_open_tickers", side_effect=side_effects), \
             patch("orchestrator.open_positions", return_value=[{"ticker": "NVDA"}]), \
             patch("orchestrator.close_all_positions"), \
             patch("agents.alpaca_broker._get", return_value=MagicMock()), \
             patch("core.ledger.log") as mock_ledger, \
             patch("core.db.insert"), \
             patch("orchestrator.send_alert"), \
             patch("time.sleep"):
            from orchestrator import _sweep_and_verify
            _sweep_and_verify()

        mock_ledger.assert_called_once()
        assert mock_ledger.call_args[0][0] == "sweep_failed"

    def test_premarket_returns_early_when_sweep_fails(self):
        """premarket() returns before pool selection when _sweep_and_verify() returns False."""
        with patch("orchestrator._is_trading_day", return_value=True), \
             patch("orchestrator._is_halted", return_value=False), \
             patch("core.db.select", return_value=[]), \
             patch("orchestrator.seed_pools_if_empty"), \
             patch("orchestrator._sweep_and_verify", return_value=False), \
             patch("orchestrator.get_pool3_with_context") as mock_pool3:
            from orchestrator import premarket
            premarket(broker="alpaca")

        mock_pool3.assert_not_called()


class TestEodPartialCloseAlertB:
    """RFN-3: EOD alert fires when ANY position fails to close, not just when ALL fail."""

    def test_alert_fires_when_one_position_stays_open(self):
        """3/4 positions close successfully but 1 remains open → alert fires."""
        open_before = [
            {"id": "p1", "ticker": "AAPL"},
            {"id": "p2", "ticker": "MSFT"},
        ]
        open_after_eod = [{"id": "p2", "ticker": "MSFT"}]  # p2 failed to close

        with patch("orchestrator._is_trading_day", return_value=True), \
             patch("orchestrator._is_halted", return_value=False), \
             patch("orchestrator._log_run_b"), \
             patch("orchestrator.open_positions", return_value=open_before), \
             patch("orchestrator.close_all_positions", return_value=["AAPL"]), \
             patch("orchestrator.reconcile_eod_stale_opens"), \
             patch("core.db.select", side_effect=[[], open_after_eod]), \
             patch("core.db.insert"), \
             patch("orchestrator.score_today", return_value={"scored": 0, "promoted": 0, "demoted": 0}), \
             patch("orchestrator.write_daily_performance"), \
             patch("orchestrator.send_alert") as mock_alert:
            from orchestrator import eod
            eod(broker="alpaca")

        mock_alert.assert_called_once()
        subject = mock_alert.call_args[0][0]
        assert "EOD close FAILED" in subject
        assert "MSFT" in mock_alert.call_args[0][1]

    def test_no_alert_when_all_positions_close(self):
        """All positions close cleanly → no alert."""
        open_before = [{"id": "p1", "ticker": "AAPL"}]

        with patch("orchestrator._is_trading_day", return_value=True), \
             patch("orchestrator._is_halted", return_value=False), \
             patch("orchestrator._log_run_b"), \
             patch("orchestrator.open_positions", return_value=open_before), \
             patch("orchestrator.close_all_positions", return_value=["AAPL"]), \
             patch("orchestrator.reconcile_eod_stale_opens"), \
             patch("core.db.select", side_effect=[[], []]), \
             patch("core.db.insert"), \
             patch("orchestrator.score_today", return_value={"scored": 0, "promoted": 0, "demoted": 0}), \
             patch("orchestrator.write_daily_performance"), \
             patch("orchestrator.send_alert") as mock_alert:
            from orchestrator import eod
            eod(broker="alpaca")

        eod_alert_calls = [
            c for c in mock_alert.call_args_list
            if "EOD close FAILED" in str(c)
        ]
        assert len(eod_alert_calls) == 0


class TestReconciliationFixesB:
    """RFN-4: date filter + limit; RFN-5: enum comparisons."""

    def test_reconcile_uses_today_start_filter(self):
        """_reconcile_with_alpaca passes after= to get_orders (RFN-4)."""
        mock_broker = MagicMock()
        mock_broker.get_all_positions.return_value = []
        mock_broker.get_orders.return_value = []

        with patch("agents.alpaca_broker._get", return_value=mock_broker), \
             patch("agents.alpaca_broker.open_positions", return_value=[{"ticker": "AAPL", "id": "1", "alpaca_order_id": None, "entry_price": 100, "shares": 10}]), \
             patch("agents.alpaca_broker.db"):
            from agents.alpaca_broker import _reconcile_with_alpaca
            _reconcile_with_alpaca()

        mock_broker.get_orders.assert_called_once()
        call_kwargs = mock_broker.get_orders.call_args[0][0]
        assert call_kwargs.limit == 500
        assert call_kwargs.after is not None, "Expected after= date filter"

    def test_reconcile_uses_getattr_for_side_enum(self):
        """Filled buy detection uses getattr(o.side, 'value', str(o.side)) (RFN-5)."""
        from agents.alpaca_broker import _reconcile_with_alpaca
        import inspect
        source = inspect.getsource(_reconcile_with_alpaca)
        assert 'str(o.side) == "buy"' not in source, (
            "RFN-5: use getattr(o.side, 'value', str(o.side)) not str(o.side)"
        )
        assert "getattr(o.side" in source

    def test_reconcile_uses_getattr_for_status_enum(self):
        """Status comparison uses getattr(o.status, 'value', ...) not str() (RFN-5)."""
        from agents.alpaca_broker import _reconcile_with_alpaca
        import inspect
        source = inspect.getsource(_reconcile_with_alpaca)
        assert 'str(o.status) == "filled"' not in source, (
            "RFN-5: use getattr(o.status, 'value', str(o.status)) not str(o.status)"
        )
        assert "getattr(o.status" in source
