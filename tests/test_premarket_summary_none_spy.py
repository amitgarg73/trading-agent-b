"""
Regression: _premarket_summary raises TypeError when spy_change_pct is None.
market_context.get() returns None for spy_change_pct when Alpaca fetch fails.
dict.get(key, default) does NOT fall back when the key exists with a None value.
"""
from orchestrator import _premarket_summary


def _base_mkt(**overrides):
    m = {
        "vix_level": 18.5,
        "fear_greed": 45,
        "fear_greed_label": "Fear",
        "futures_bias": "NEUTRAL",
        "spy_change_pct": 0.42,
        "sector_rotation": {},
    }
    m.update(overrides)
    return m


def test_summary_renders_when_spy_pct_none_in_mkt():
    """spy_change_pct key present but None — must not raise."""
    result = _premarket_summary(
        run_time="2026-06-04 09:35 ET",
        pool3_tickers=["AAPL", "MSFT"],
        mkt=_base_mkt(spy_change_pct=None),
        spy_pct=None,
        n_after_scan=5,
        n_after_filters=2,
        n_sent_to_claude=2,
        candidates_sent=[],
        trades_selected=[],
        risk_rejected=[],
        guard_rejected=[],
        final=[],
        claude_reasoning="No trades.",
    )
    assert "SPY" in result
    assert "+0.00%" in result


def test_summary_renders_when_spy_pct_provided_as_fallback():
    """spy_change_pct absent from mkt; spy_pct param used instead."""
    mkt = _base_mkt()
    del mkt["spy_change_pct"]
    result = _premarket_summary(
        run_time="2026-06-04 09:35 ET",
        pool3_tickers=[],
        mkt=mkt,
        spy_pct=1.23,
        n_after_scan=0,
        n_after_filters=0,
        n_sent_to_claude=0,
        candidates_sent=[],
        trades_selected=[],
        risk_rejected=[],
        guard_rejected=[],
        final=[],
        claude_reasoning="No trades.",
    )
    assert "+1.23%" in result


def test_summary_renders_zero_spy_correctly():
    """spy_change_pct=0.0 (flat day) must not fall back to spy_pct."""
    result = _premarket_summary(
        run_time="2026-06-04 09:35 ET",
        pool3_tickers=[],
        mkt=_base_mkt(spy_change_pct=0.0),
        spy_pct=99.9,
        n_after_scan=0,
        n_after_filters=0,
        n_sent_to_claude=0,
        candidates_sent=[],
        trades_selected=[],
        risk_rejected=[],
        guard_rejected=[],
        final=[],
        claude_reasoning="No trades.",
    )
    assert "+0.00%" in result
    assert "99" not in result
