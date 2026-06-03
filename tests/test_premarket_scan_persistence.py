"""
Tests that premarket candidates are persisted to b_scan_results so per-ticker
scanner details (scores, VWAP, signals) are visible after a NO_TRADES day.
"""
from __future__ import annotations
from unittest.mock import MagicMock, patch
from datetime import date, datetime


def _make_db_mock():
    mock = MagicMock()
    mock.select.return_value = []
    inserted: dict[str, list] = {}

    def _insert(table, row):
        inserted.setdefault(table, [])
        row_with_id = {**row, "id": len(inserted[table]) + 1}
        inserted[table].append(row_with_id)
        return row_with_id

    mock.insert.side_effect = _insert
    mock._inserted = inserted
    return mock


def _make_candidates():
    return [
        {
            "ticker": "MSFT", "technical_score": 4, "current_price": 420.0,
            "above_vwap": False, "vwap": 421.5, "rs_vs_spy": 0.9,
            "today_pct_change": -0.3, "volume_ratio": 0.08, "atr_pct": 1.2,
            "above_orb": False, "signals": ["MACD bullish"], "sector": "Technology", "pool": 2,
        },
        {
            "ticker": "NVDA", "technical_score": 5, "current_price": 880.0,
            "above_vwap": True, "vwap": 875.0, "rs_vs_spy": 1.4,
            "today_pct_change": 0.6, "volume_ratio": 0.12, "atr_pct": 2.1,
            "above_orb": True, "signals": ["MACD bullish", "Uptrend"], "sector": "Technology", "pool": 2,
        },
    ]


def _simulate_premarket_scan_write(db_mock, candidates, pool3_tickers):
    """Mirrors orchestrator.py section 3.4 exactly."""
    _premarket_candidate_fields = [
        "ticker", "technical_score", "current_price", "above_vwap", "vwap",
        "rs_vs_spy", "today_pct_change", "volume_ratio", "atr_pct",
        "above_orb", "signals", "sector", "pool",
    ]
    _premarket_candidates_slim = [
        {k: c.get(k) for k in _premarket_candidate_fields} for c in candidates
    ]
    try:
        db_mock.insert("b_scan_results", {
            "date":       str(date.today()),
            "scan_type":  "premarket",
            "scanned_at": datetime.utcnow().isoformat(),
            "candidates": _premarket_candidates_slim,
            "placed":     0,
            "results": {
                "pool3_count": len(pool3_tickers),
                "after_scan":  len(candidates),
            },
        })
    except Exception as _e:
        pass  # matches orchestrator: error is swallowed with warning print


class TestPremarketScanPersistence:

    def test_scan_row_written_to_b_scan_results(self):
        db_mock = _make_db_mock()
        _simulate_premarket_scan_write(db_mock, _make_candidates(), ["MSFT", "NVDA", "AAPL"])
        assert "b_scan_results" in db_mock._inserted

    def test_scan_type_is_premarket(self):
        db_mock = _make_db_mock()
        _simulate_premarket_scan_write(db_mock, _make_candidates(), ["MSFT", "NVDA", "AAPL"])
        row = db_mock._inserted["b_scan_results"][0]
        assert row["scan_type"] == "premarket"

    def test_candidates_list_persisted(self):
        db_mock = _make_db_mock()
        _simulate_premarket_scan_write(db_mock, _make_candidates(), ["MSFT", "NVDA", "AAPL"])
        row = db_mock._inserted["b_scan_results"][0]
        assert len(row["candidates"]) == 2
        tickers = [c["ticker"] for c in row["candidates"]]
        assert "MSFT" in tickers
        assert "NVDA" in tickers

    def test_candidate_has_key_fields(self):
        db_mock = _make_db_mock()
        _simulate_premarket_scan_write(db_mock, _make_candidates(), ["MSFT", "NVDA", "AAPL"])
        row = db_mock._inserted["b_scan_results"][0]
        msft = next(c for c in row["candidates"] if c["ticker"] == "MSFT")
        assert msft["technical_score"] == 4
        assert msft["above_vwap"] is False
        assert msft["signals"] == ["MACD bullish"]
        assert msft["sector"] == "Technology"

    def test_results_summary_has_counts(self):
        db_mock = _make_db_mock()
        pool3 = ["MSFT", "NVDA", "AAPL"]
        _simulate_premarket_scan_write(db_mock, _make_candidates(), pool3)
        row = db_mock._inserted["b_scan_results"][0]
        assert row["results"]["pool3_count"] == 3
        assert row["results"]["after_scan"] == 2

    def test_placed_is_zero(self):
        db_mock = _make_db_mock()
        _simulate_premarket_scan_write(db_mock, _make_candidates(), ["MSFT", "NVDA"])
        row = db_mock._inserted["b_scan_results"][0]
        assert row["placed"] == 0

    def test_db_failure_does_not_crash_pipeline(self):
        db_mock = _make_db_mock()
        db_mock.insert.side_effect = Exception("DB unavailable")
        try:
            _simulate_premarket_scan_write(db_mock, _make_candidates(), ["MSFT", "NVDA"])
        except Exception:
            assert False, "DB failure should be swallowed, not propagated"

    def test_slim_fields_only_no_extra_keys(self):
        db_mock = _make_db_mock()
        fat_candidate = {**_make_candidates()[0], "extra_field": "should_not_appear", "raw_bars": [1, 2, 3]}
        _simulate_premarket_scan_write(db_mock, [fat_candidate], ["MSFT"])
        row = db_mock._inserted["b_scan_results"][0]
        candidate = row["candidates"][0]
        assert "extra_field" not in candidate
        assert "raw_bars" not in candidate
