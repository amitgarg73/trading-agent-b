"""
Guardrails — final sanity checks before order placement.
Rejects trades with obviously wrong prices, missing fields, or calculation errors.
"""
from __future__ import annotations
from config.settings import TARGET_PCT, MAX_LOSS_PER_TRADE, MIN_REWARD_RISK, PRICE_SANITY_PCT


REQUIRED_FIELDS = ["ticker", "action", "entry_price", "target_price",
                   "stop_loss", "shares", "position_size", "confidence"]


def check(trades: list[dict]) -> tuple[list[dict], list[str]]:
    """Returns (passed_trades, rejection_reasons)."""
    passed, rejected = [], []

    for t in trades:
        ticker = t.get("ticker", "?")
        reason = _validate(t)
        if reason:
            rejected.append(f"{ticker}: {reason}")
        else:
            passed.append(t)

    if rejected:
        print(f"[guardrails] Blocked: {rejected}")
    return passed, rejected


def _validate(trade: dict) -> str | None:
    ticker = trade.get("ticker", "?")

    # Required fields
    for f in REQUIRED_FIELDS:
        if trade.get(f) is None:
            return f"missing field: {f}"

    entry  = float(trade["entry_price"])
    target = float(trade["target_price"])
    stop   = float(trade["stop_loss"])
    shares = int(trade["shares"])
    ps     = float(trade["position_size"])

    if entry <= 0:
        return "entry_price <= 0"
    if shares <= 0:
        return "shares <= 0"
    if target <= entry:
        return f"target {target} <= entry {entry}"
    if stop >= entry:
        return f"stop {stop} >= entry {entry}"

    # Verify target matches formula within 2%
    expected_target = round(entry * (1 + TARGET_PCT), 2)
    if abs(target - expected_target) / expected_target > 0.02:
        return f"target {target} deviates from formula {expected_target}"

    # Verify stop matches formula within 2%
    expected_stop = round(entry * (1 - MAX_LOSS_PER_TRADE), 2)
    if abs(stop - expected_stop) / expected_stop > 0.02:
        return f"stop {stop} deviates from formula {expected_stop}"

    # R:R check
    profit  = shares * (target - entry)
    loss    = shares * (entry - stop)
    if loss <= 0:
        return "max_loss is zero or negative"
    rr = profit / loss
    if rr < MIN_REWARD_RISK:
        return f"R:R {rr:.2f} < minimum {MIN_REWARD_RISK}"

    return None
