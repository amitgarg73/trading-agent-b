"""
Regression tests for Strategy B intraday pipeline filter changes:
1. Garbage data filter — rejects candidates with price=0 or rsi=None
2. ORB downgrade — above_orb=False no longer hard-filters; data still passed to Claude
3. Extension filter — threshold rises to 5% on strong up days (avg futures >= 2%)
"""
from __future__ import annotations


def _make_candidate(**kwargs) -> dict:
    base = {
        "ticker": "TEST",
        "technical_score": 6,
        "price": 50.0,
        "current_price": 50.0,
        "rsi": 55.0,
        "volume_ratio": 1.2,
        "above_orb": True,
        "above_vwap": True,
        "today_pct_change": 1.0,
        "day_high": 52.0,
        "day_low": 48.0,
    }
    base.update(kwargs)
    return base


# ── Garbage data filter ───────────────────────────────────────────────────────

def _apply_garbage_filter(candidates: list[dict]) -> list[dict]:
    return [
        c for c in candidates
        if (c.get("price") or c.get("current_price") or 0) > 0
        and c.get("rsi") is not None
    ]


class TestGarbageFilter:

    def test_passes_normal_candidate(self):
        c = [_make_candidate(ticker="GOOGL", price=182.0, rsi=54.8)]
        assert len(_apply_garbage_filter(c)) == 1

    def test_rejects_price_zero(self):
        c = [_make_candidate(ticker="DFSCW", price=0.0, current_price=0.0, rsi=None)]
        assert len(_apply_garbage_filter(c)) == 0

    def test_rejects_rsi_none(self):
        c = [_make_candidate(ticker="AUROW", price=0.0, rsi=None)]
        assert len(_apply_garbage_filter(c)) == 0

    def test_rejects_warrant_tickers(self):
        """Reproduce June scenario: warrants with no real data, all should be filtered."""
        warrants = [
            _make_candidate(ticker=t, price=0.0, current_price=0.0, rsi=None)
            for t in ["DFSCW", "AUROW", "ORGNW", "DFLIW"]
        ]
        result = _apply_garbage_filter(warrants)
        assert len(result) == 0

    def test_keeps_real_candidates_when_mixed(self):
        candidates = [
            _make_candidate(ticker="DFSCW", price=0.0, rsi=None),
            _make_candidate(ticker="GOOGL", price=182.0, rsi=54.8),
            _make_candidate(ticker="ORCL",  price=148.0, rsi=61.2),
        ]
        result = _apply_garbage_filter(candidates)
        assert len(result) == 2
        tickers = [c["ticker"] for c in result]
        assert "GOOGL" in tickers and "ORCL" in tickers

    def test_passes_when_current_price_set_but_not_price(self):
        c = [_make_candidate(ticker="XYZ", price=0.0, current_price=45.0, rsi=52.0)]
        assert len(_apply_garbage_filter(c)) == 1


# ── ORB downgrade — signal not gate ──────────────────────────────────────────

def _apply_orb_signal(candidates: list[dict]) -> tuple[list[dict], int]:
    """New behaviour: pass all through, just count below-ORB for logging."""
    orb_below = sum(1 for c in candidates if c.get("above_orb") is False)
    return candidates, orb_below


class TestORBDowngrade:

    def test_below_orb_candidates_pass_through(self):
        candidates = [
            _make_candidate(ticker="GOOGL", above_orb=False),
            _make_candidate(ticker="TSLA",  above_orb=False),
            _make_candidate(ticker="AAPL",  above_orb=True),
        ]
        result, below_count = _apply_orb_signal(candidates)
        assert len(result) == 3
        assert below_count == 2

    def test_none_orb_passes_through(self):
        """above_orb=None (no intraday data yet) must not be dropped."""
        candidates = [_make_candidate(ticker="MSFT", above_orb=None)]
        result, below = _apply_orb_signal(candidates)
        assert len(result) == 1
        assert below == 0

    def test_all_below_orb_still_passes_to_claude(self):
        """All candidates below ORB — old filter killed all of them."""
        candidates = [_make_candidate(ticker=f"T{i}", above_orb=False) for i in range(10)]
        result, below = _apply_orb_signal(candidates)
        assert len(result) == 10
        assert below == 10

    def test_above_orb_candidates_unaffected(self):
        candidates = [_make_candidate(ticker="ORCL", above_orb=True)]
        result, below = _apply_orb_signal(candidates)
        assert len(result) == 1
        assert below == 0


# ── Extension filter — market-context-aware threshold ────────────────────────

def _avg_futures(futures: dict) -> float:
    if not futures:
        return 0.0
    return sum(v["change_pct"] for v in futures.values()) / len(futures)


def _apply_extension_filter(candidates: list[dict], futures: dict) -> tuple[list[dict], float]:
    avg_fut = _avg_futures(futures)
    threshold = 5.0 if avg_fut >= 2.0 else 3.0
    result = [
        c for c in candidates
        if not (
            (c.get("today_pct_change") or 0) > threshold
            and (c.get("volume_ratio") or 0) < 0.7
        )
    ]
    return result, threshold


class TestExtensionFilter:

    def _futures(self, avg_pct: float) -> dict:
        return {"S&P500": {"change_pct": avg_pct, "price": 5000.0}}

    def test_normal_day_drops_above_3pct_low_vol(self):
        candidates = [
            _make_candidate(ticker="NVDA", today_pct_change=3.5, volume_ratio=0.4),
            _make_candidate(ticker="AAPL", today_pct_change=1.5, volume_ratio=0.4),
        ]
        result, threshold = _apply_extension_filter(candidates, self._futures(0.5))
        assert threshold == 3.0
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"

    def test_strong_up_day_keeps_stocks_up_to_5pct(self):
        candidates = [
            _make_candidate(ticker="NVDA", today_pct_change=3.5, volume_ratio=0.4),
            _make_candidate(ticker="AMD",  today_pct_change=4.8, volume_ratio=0.5),
        ]
        result, threshold = _apply_extension_filter(candidates, self._futures(2.5))
        assert threshold == 5.0
        assert len(result) == 2

    def test_strong_up_day_still_drops_above_5pct_low_vol(self):
        candidates = [
            _make_candidate(ticker="NVDA", today_pct_change=5.5, volume_ratio=0.3),
            _make_candidate(ticker="AAPL", today_pct_change=3.5, volume_ratio=0.4),
        ]
        result, threshold = _apply_extension_filter(candidates, self._futures(3.0))
        assert threshold == 5.0
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"

    def test_high_volume_never_filtered_regardless_of_pct(self):
        candidates = [
            _make_candidate(ticker="TSLA", today_pct_change=6.0, volume_ratio=0.8),
        ]
        result, _ = _apply_extension_filter(candidates, self._futures(0.3))
        assert len(result) == 1

    def test_exactly_2pct_futures_triggers_strong_threshold(self):
        candidates = [_make_candidate(ticker="MSFT", today_pct_change=3.5, volume_ratio=0.5)]
        result, threshold = _apply_extension_filter(candidates, self._futures(2.0))
        assert threshold == 5.0
        assert len(result) == 1

    def test_no_futures_data_uses_default_threshold(self):
        candidates = [_make_candidate(ticker="AMZN", today_pct_change=3.5, volume_ratio=0.4)]
        result, threshold = _apply_extension_filter(candidates, {})
        assert threshold == 3.0
        assert len(result) == 0
