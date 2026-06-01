"""
ATR-based stop sizing (P0-1) and ORB choppiness gate (P0-2).
Applied after sector guard, before guardrails.

P0-1: stop = max(atr_pct × 1.2, 0.5%). Shares from constant $150 dollar risk,
      capped at confidence position limit. Replaces fixed 0.67% formula stop
      so the stop survives normal intraday noise rather than firing on the first wick.

P0-2: If first-30-min opening range < 0.5 × ATR, the open was choppy (no
      directional conviction). Halve shares for those names.

Trades where ATR stop ≥ target (R:R < 1) are dropped and returned as reasons.
Trades where the ATR-adjusted R:R falls below min_rr are also dropped — risk
validated the original prices; if ATR widens the stop below the floor, the trade
is not viable and should not be placed.
"""
from __future__ import annotations
import yfinance as yf
from config.settings import (
    ATR_STOP_MULTIPLIER, ATR_STOP_FLOOR, MAX_LOSS_DOLLARS,
    ORB_ATR_FLOOR, POSITION_SIZE_BY_CONFIDENCE, MIN_REWARD_RISK,
)

_orb_cache: dict[str, float | None] = {}


def _fetch_orb_pct(ticker: str, entry: float) -> float | None:
    """Opening range (9:30–9:59 AM ET) as fraction of price. Cached per process."""
    if ticker in _orb_cache:
        return _orb_cache[ticker]
    try:
        df = yf.Ticker(ticker).history(period="1d", interval="5m")
        if df.empty:
            _orb_cache[ticker] = None
            return None
        idx = df.index
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        df.index = idx.tz_convert("America/New_York")
        orb = df.between_time("09:30", "09:59")
        if len(orb) < 2:
            _orb_cache[ticker] = None
            return None
        orb_range = float(orb["High"].max() - orb["Low"].min())
        result = orb_range / entry if entry > 0 else 0.0
        _orb_cache[ticker] = result
        return result
    except Exception:
        _orb_cache[ticker] = None
        return None


_MIN_POSITION_SIZE = float(min(POSITION_SIZE_BY_CONFIDENCE.values()))


def apply(trades: list[dict],
          candidates_atr: dict[str, float | None],
          min_rr: float = MIN_REWARD_RISK) -> tuple[list[dict], list[str]]:
    """
    Apply ATR-based stop and ORB gate to approved trades.

    candidates_atr: ticker → atr_pct (percentage, e.g. 1.5 means 1.5%)
    min_rr: minimum R:R after ATR adjustment — pass QUIET_DAY_MIN_REWARD_RISK on quiet days.
    Returns (adjusted_trades, dropped_reasons).
    Trades with no ATR data pass through unchanged.
    """
    adjusted: list[dict] = []
    dropped:  list[str]  = []

    for t in trades:
        ticker     = t["ticker"]
        atr_pct    = candidates_atr.get(ticker)
        if not atr_pct or atr_pct <= 0:
            print(f"  [atr_sizer] {ticker}: no ATR — keeping formula stop")
            adjusted.append(t)
            continue

        entry      = float(t["entry_price"])
        target     = float(t["target_price"])
        confidence = (t.get("confidence") or "MEDIUM").upper()
        size_cap   = float(POSITION_SIZE_BY_CONFIDENCE.get(confidence, 3_000))

        # P0-1: ATR-based stop
        stop_pct   = max((atr_pct / 100.0) * ATR_STOP_MULTIPLIER, ATR_STOP_FLOOR)
        stop       = round(entry * (1.0 - stop_pct), 2)
        target_pct = (target - entry) / entry

        if stop_pct >= target_pct:
            reason = (f"{ticker}: ATR stop {stop_pct*100:.1f}% ≥ target "
                      f"{target_pct*100:.1f}% (ATR {atr_pct:.1f}%) — not viable")
            print(f"  [atr_sizer] {reason}")
            dropped.append(reason)
            continue

        # Shares: constant $150 risk OR position cap, whichever is smaller
        shares_by_risk = max(1, int(MAX_LOSS_DOLLARS / (entry * stop_pct)))
        shares_by_size = max(1, int(size_cap / entry))
        shares         = min(shares_by_risk, shares_by_size)
        position_size  = round(shares * entry, 2)

        # Size floor check BEFORE ORB halving — ORB is intentional risk reduction, not a quality gate.
        # Enforce R:R here too (based on full-size shares before halving).
        est_profit = round(shares * (target - entry), 2)
        max_loss   = round(shares * (entry - stop), 2)
        rr         = round(est_profit / max_loss, 2) if max_loss > 0 else 0.0

        if rr < min_rr:
            reason = f"{ticker}: R:R {rr:.2f} below {min_rr} after ATR stop — dropped"
            print(f"  [atr_sizer] {reason}")
            dropped.append(reason)
            continue
        if position_size < _MIN_POSITION_SIZE:
            reason = f"{ticker}: size ${position_size:,.0f} below min ${_MIN_POSITION_SIZE:,.0f} after ATR sizing — dropped"
            print(f"  [atr_sizer] {reason}")
            dropped.append(reason)
            continue

        # P0-2: ORB choppiness gate — halve shares for choppy opens; size floor already cleared above
        orb_pct = _fetch_orb_pct(ticker, entry)
        choppy  = orb_pct is not None and orb_pct < (atr_pct / 100.0) * ORB_ATR_FLOOR
        if choppy:
            shares        = max(1, shares // 2)
            position_size = round(shares * entry, 2)
            est_profit    = round(shares * (target - entry), 2)
            max_loss      = round(shares * (entry - stop), 2)
            rr            = round(est_profit / max_loss, 2) if max_loss > 0 else 0.0

        print(
            f"  [atr_sizer] {ticker}: stop ${t.get('stop_loss', entry):.2f}→${stop:.2f} "
            f"({stop_pct*100:.1f}%, ATR={atr_pct:.1f}%), "
            f"shares {t.get('shares', 0)}→{shares}, "
            f"size ${t.get('position_size', 0):,.0f}→${position_size:,.0f}, "
            f"R:R {rr:.2f}"
            + (" [ORB choppy — halved]" if choppy else "")
        )

        adjusted.append({
            **t,
            "stop_loss":        stop,
            "shares":           shares,
            "position_size":    position_size,
            "estimated_profit": est_profit,
            "max_loss":         max_loss,
            "reward_risk":      rr,
            "atr_stop_pct":     round(stop_pct, 5),
            "orb_choppy":       choppy,
        })

    return adjusted, dropped
