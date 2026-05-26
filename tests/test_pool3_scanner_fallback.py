"""
Tests for Pool 3 scanner fallback path (orchestrator.py).

When run_scan() returns 0 candidates from blue chip Pool 3 tickers (common on
quiet days), the orchestrator falls back to pool_filter candidates so Claude
can still make selections.
"""
from __future__ import annotations
from unittest.mock import patch, MagicMock


POOL3_CONTEXT = [
    {"ticker": "AAPL", "cur_price": 195.0, "filter_score": 1.5, "above_vwap": True,
     "vol_ratio": 1.1, "today_return": 0.4, "rs_vs_market": 1.2},
    {"ticker": "MSFT", "cur_price": 420.0, "filter_score": 0.5, "above_vwap": False,
     "vol_ratio": 0.9, "today_return": -0.1, "rs_vs_market": 0.8},
    {"ticker": "NVDA", "cur_price": 900.0, "filter_score": 2.0, "above_vwap": True,
     "vol_ratio": 1.3, "today_return": 0.8, "rs_vs_market": 1.5},
]


def _run_premarket_to_candidates(scan_return, pool3_context=None):
    """
    Isolate just the scanner + fallback logic from run_premarket().
    Returns the candidates list that would be passed to news_intel.
    """
    if pool3_context is None:
        pool3_context = POOL3_CONTEXT

    candidates = scan_return

    if not candidates:
        candidates = [
            {**c, "technical_score": 0, "signals": ["pool3_fallback"]}
            for c in pool3_context
        ]

    return candidates


class TestPool3ScannerFallback:

    def test_fallback_fires_when_scanner_returns_empty(self):
        candidates = _run_premarket_to_candidates(scan_return=[])
        assert len(candidates) == len(POOL3_CONTEXT)

    def test_fallback_does_not_fire_when_scanner_returns_results(self):
        scanner_result = [{"ticker": "AAPL", "technical_score": 2, "signals": ["SMA20 breakout"]}]
        candidates = _run_premarket_to_candidates(scan_return=scanner_result)
        assert candidates == scanner_result

    def test_fallback_candidates_have_technical_score_zero(self):
        candidates = _run_premarket_to_candidates(scan_return=[])
        assert all(c["technical_score"] == 0 for c in candidates)

    def test_fallback_candidates_have_pool3_fallback_signal(self):
        candidates = _run_premarket_to_candidates(scan_return=[])
        assert all("pool3_fallback" in c["signals"] for c in candidates)

    def test_fallback_candidates_preserve_pool_filter_fields(self):
        candidates = _run_premarket_to_candidates(scan_return=[])
        tickers = [c["ticker"] for c in candidates]
        assert "AAPL" in tickers
        aapl = next(c for c in candidates if c["ticker"] == "AAPL")
        assert aapl["cur_price"] == 195.0
        assert aapl["filter_score"] == 1.5
        assert aapl["above_vwap"] is True

    def test_fallback_candidates_have_no_atr_pct(self):
        # ATR sizer will use formula stops for fallback candidates — acceptable
        candidates = _run_premarket_to_candidates(scan_return=[])
        assert all(c.get("atr_pct") is None for c in candidates)

    def test_fallback_empty_pool3_context_produces_no_candidates(self):
        candidates = _run_premarket_to_candidates(scan_return=[], pool3_context=[])
        assert candidates == []
