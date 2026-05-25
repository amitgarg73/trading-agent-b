"""
Tests for RF-2 in Strategy B: _reconcile_with_alpaca() in agents/alpaca_broker.py.

When get_orders() raises, must: log to ledger, write to b_scan_results, send alert, return early.
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime


OPEN_POS = {
    "id":              "pos-b-rf2",
    "ticker":          "AAPL",
    "status":          "OPEN",
    "entry_price":     180.0,
    "fill_price":      180.0,
    "shares":          20,
    "alpaca_order_id": "order-b-rf2",
}


def _run_reconcile_with_broken_orders(tmp_path, monkeypatch):
    import core.ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "_DATA_DIR", tmp_path)

    from agents.alpaca_broker import _reconcile_with_alpaca

    with patch("agents.alpaca_broker.get_open_tickers", return_value=set()), \
         patch("agents.alpaca_broker._get") as mock_get, \
         patch("agents.alpaca_broker.open_positions", return_value=[OPEN_POS]), \
         patch("core.db.insert") as mock_ins, \
         patch("core.alerts.send_alert") as mock_alert, \
         patch("alpaca.trading.requests.GetOrdersRequest"), \
         patch("alpaca.trading.enums.QueryOrderStatus"):

        mock_get.return_value.get_orders.side_effect = Exception("Alpaca timeout B")
        _reconcile_with_alpaca()

    return mock_ins, mock_alert, ledger_mod


class TestReconcileFailureB:

    def test_returns_early_on_exception(self, monkeypatch, tmp_path):
        """No positions should be marked CLOSED/UNFILLED when get_orders fails."""
        with patch("agents.alpaca_broker.get_open_tickers", return_value=set()), \
             patch("agents.alpaca_broker._get") as mock_get, \
             patch("agents.alpaca_broker.open_positions", return_value=[OPEN_POS]), \
             patch("core.db.insert") as mock_ins, \
             patch("core.db.update") as mock_upd, \
             patch("core.alerts.send_alert"), \
             patch("core.ledger.log"), \
             patch("alpaca.trading.requests.GetOrdersRequest"), \
             patch("alpaca.trading.enums.QueryOrderStatus"):

            mock_get.return_value.get_orders.side_effect = Exception("timeout")
            from agents.alpaca_broker import _reconcile_with_alpaca
            _reconcile_with_alpaca()

        mock_upd.assert_not_called()

    def test_logs_reconcile_failed_to_ledger(self, monkeypatch, tmp_path):
        _, _, ledger_mod = _run_reconcile_with_broken_orders(tmp_path, monkeypatch)
        events = ledger_mod.read_today()
        assert any(e["event"] == "reconcile_failed" for e in events)
        fail_ev = next(e for e in events if e["event"] == "reconcile_failed")
        assert "Alpaca timeout B" in fail_ev["data"]["error"]

    def test_writes_reconcile_failed_to_b_scan_results(self, monkeypatch, tmp_path):
        mock_ins, _, _ = _run_reconcile_with_broken_orders(tmp_path, monkeypatch)
        db_calls = [c for c in mock_ins.call_args_list if c[0][0] == "b_scan_results"]
        assert len(db_calls) == 1
        assert db_calls[0][0][1]["scan_type"] == "reconcile_failed"

    def test_sends_alert_on_failure(self, monkeypatch, tmp_path):
        _, mock_alert, _ = _run_reconcile_with_broken_orders(tmp_path, monkeypatch)
        mock_alert.assert_called_once()
        subject = mock_alert.call_args[0][0]
        assert "Reconciliation" in subject or "reconcil" in subject.lower()
