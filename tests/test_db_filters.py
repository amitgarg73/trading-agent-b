"""
Tests for core/db.select() filters_gte and filters_lte parameters.

These parameters were added to push date-range filters to Supabase
instead of loading all historical positions and filtering in Python.
"""
from unittest.mock import MagicMock, patch, call
import pytest


def _make_mock_client():
    """Build a mock Supabase client that chains .gte()/.lte() calls."""
    mock_data = MagicMock()
    mock_data.data = [{"id": 1, "closed_at": "2026-05-26T14:00:00"}]

    mock_execute = MagicMock(return_value=mock_data)
    mock_chain = MagicMock()
    mock_chain.execute = mock_execute
    mock_chain.eq.return_value = mock_chain
    mock_chain.gte.return_value = mock_chain
    mock_chain.lte.return_value = mock_chain
    mock_chain.order.return_value = mock_chain
    mock_chain.limit.return_value = mock_chain
    mock_chain.is_.return_value = mock_chain
    mock_chain.select.return_value = mock_chain

    mock_table = MagicMock()
    mock_table.select.return_value = mock_chain

    mock_client = MagicMock()
    mock_client.table.return_value = mock_table

    return mock_client, mock_chain


class TestDbSelectFilters:

    def _call_select(self, mock_client, **kwargs):
        with patch("core.db._get", return_value=mock_client):
            from core import db
            return db.select("b_positions", **kwargs)

    def test_filters_gte_calls_gte_on_query(self):
        mock_client, mock_chain = _make_mock_client()
        self._call_select(mock_client, filters_gte={"closed_at": "2026-05-26T00:00:00"})
        mock_chain.gte.assert_called_once_with("closed_at", "2026-05-26T00:00:00")

    def test_filters_lte_calls_lte_on_query(self):
        mock_client, mock_chain = _make_mock_client()
        self._call_select(mock_client, filters_lte={"closed_at": "2026-05-26T23:59:59"})
        mock_chain.lte.assert_called_once_with("closed_at", "2026-05-26T23:59:59")

    def test_filters_gte_and_lte_both_applied(self):
        mock_client, mock_chain = _make_mock_client()
        self._call_select(mock_client,
                          filters_gte={"closed_at": "2026-05-26T00:00:00"},
                          filters_lte={"closed_at": "2026-05-26T23:59:59"})
        mock_chain.gte.assert_called_once_with("closed_at", "2026-05-26T00:00:00")
        mock_chain.lte.assert_called_once_with("closed_at", "2026-05-26T23:59:59")

    def test_filters_gte_and_eq_both_applied(self):
        mock_client, mock_chain = _make_mock_client()
        self._call_select(mock_client,
                          filters={"status": "CLOSED"},
                          filters_gte={"closed_at": "2026-05-26T00:00:00"})
        mock_chain.eq.assert_called_once_with("status", "CLOSED")
        mock_chain.gte.assert_called_once_with("closed_at", "2026-05-26T00:00:00")

    def test_no_gte_lte_skips_those_calls(self):
        mock_client, mock_chain = _make_mock_client()
        self._call_select(mock_client, filters={"status": "OPEN"})
        mock_chain.gte.assert_not_called()
        mock_chain.lte.assert_not_called()

    def test_returns_data_from_execute(self):
        mock_client, mock_chain = _make_mock_client()
        result = self._call_select(mock_client,
                                   filters_gte={"closed_at": "2026-05-26T00:00:00"})
        assert result == [{"id": 1, "closed_at": "2026-05-26T14:00:00"}]
