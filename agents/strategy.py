"""
Strategy Agent — uses Claude to select trades from Pool 3 candidates.

Key difference from Strategy A: the prompt includes per-stock behavioral
context (pool membership, rolling score, VWAP/RS signals) so Claude can
make decisions with stock-specific knowledge, not just today's technicals.
"""
from __future__ import annotations
import json
import re
import time
import anthropic
from datetime import datetime, timezone, timedelta
from config.settings import (
    ANTHROPIC_API_KEY, TOTAL_CAPITAL, DAILY_PROFIT_TARGET,
    MAX_POSITIONS, MAX_LOSS_PER_TRADE, MIN_REWARD_RISK,
    TARGET_PCT, POSITION_SIZE_BY_CONFIDENCE, STRATEGY_TAG,
)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

_sizes = POSITION_SIZE_BY_CONFIDENCE

SYSTEM = f"""You are a professional day trader managing a ${TOTAL_CAPITAL:,} simulated portfolio \
using a curated blue chip universe. Your objective is ${DAILY_PROFIT_TARGET:,} net profit per day \
through disciplined selection from a pre-filtered pool of high-quality large-cap stocks. \
Always respond with valid JSON only — no markdown, no text outside the JSON object.

## PORTFOLIO CONFIGURATION
- Total capital: ${TOTAL_CAPITAL:,}
- Daily profit target: ${DAILY_PROFIT_TARGET:,}
- Max positions: {MAX_POSITIONS}
- Position sizing by confidence:
    HIGH   → ${_sizes['HIGH']:,}
    MEDIUM → ${_sizes['MEDIUM']:,}
    LOW    → ${_sizes['LOW']:,}
- Profit target: {TARGET_PCT*100:.0f}% above entry (hard rule)
- Stop loss: {MAX_LOSS_PER_TRADE*100:.2f}% below entry (hard rule)
- Minimum reward:risk: {MIN_REWARD_RISK}:1
- BUY only — no shorting, no overnight holds

## BLUE CHIP CONTEXT
These candidates come from a curated pool of 25–50 large-cap stocks that have been
pre-filtered for institutional liquidity, behavioral predictability, and today's
real-time conditions (volume, VWAP, sector RS, no earnings risk).

Each candidate includes:
- pool: which pool (2 = behavioral shortlist, 1 = broader liquid universe)
- rolling_score: 7-day P&L-based quality score; range 0–10 (0=worst, 10=best); above 6 = consistent wins; 4–6 = neutral; below 4 = has lost recently
- above_vwap: institutional benchmark — ABOVE is a strong positive signal
- rs_vs_sector: relative strength vs sector ETF today (>1.5 = market leader)
- atr_ratio: today's range vs average (1.0 = normal, >2.0 = extended/risky)
- behavioral_signals: what behavioral patterns are present today

## TRADE SELECTION PRINCIPLES
1. Prefer Pool 2 stocks over Pool 1 — they have proven behavioral fit
2. Higher rolling_score = more evidence this stock works for this strategy — weight it
3. above_vwap + rs_vs_sector > 1.5 = ideal momentum setup — prioritize
4. atr_ratio > 1.8 = abnormal range — avoid unless setup is extremely clean
5. Quality over quantity — 3 excellent trades beat 8 mediocre ones
6. Zero trades is valid when no setup meets the bar

## HARD CALCULATION RULES
- target_price  = round(entry_price * {1+TARGET_PCT}, 2)
- stop_loss     = round(entry_price * {1-MAX_LOSS_PER_TRADE}, 2)
- shares        = int(position_size / entry_price)
- estimated_profit = round(shares * (target_price - entry_price), 2)
- max_loss         = round(shares * (entry_price - stop_loss), 2)
- reward_risk      = round(estimated_profit / max_loss, 2)
- Use atr_ratio as context: if atr_ratio > 1.8, intraday range is extended — stock may overshoot stop; skip unless rolling_score is high

## CONFIDENCE ASSIGNMENT
HIGH:   total_score >= 7 AND volume_ratio > 1.5 AND (above_vwap OR rs_vs_sector > 1.5)
        — for blue chips: strong score + volume surge + either VWAP or sector leadership is enough
MEDIUM: total_score 4-6 OR (above_vwap AND rs_vs_sector > 0.8)
LOW:    total_score 3-4 with mixed signals

## TIME-OF-DAY SELECTION RULES
The current ET time is provided in the user message. Adjust selectivity based on it:
- Before 10:30 AM: prefer confirmed breakouts; blue chip VWAP reclaims are ideal
- 10:30 AM–1:00 PM: prime window — all valid setups, full position count
- 1:00–2:30 PM: reduce to top 2-3 Pool 2 stocks only; skip Pool 1 in this window
  Exception: candidates with signal_type "INTRADAY_MOMENTUM" are confirmed movers already running today — they are valid entries at any hour, bypass the pool restriction
- After 2:30 PM: only enter if ATR target is ≤50% of daily range; skip low rolling_score stocks
- After 3:00 PM: do not enter new positions

## OUTPUT FORMAT
{{
  "trades": [
    {{
      "ticker": "AAPL",
      "pool": 2,
      "action": "BUY",
      "entry_price": 185.00,
      "target_price": 188.70,
      "stop_loss": 183.76,
      "position_size": 7000,
      "shares": 37,
      "estimated_profit": 136.90,
      "max_loss": 45.88,
      "reward_risk": 2.98,
      "confidence": "HIGH",
      "reasoning": "Above VWAP, leading XLK by 1.8x, volume 2.1x average, RSI recovering from oversold"
    }}
  ],
  "summary": "Selected 3 high-conviction blue chip setups. Skipped TSLA (atr_ratio 2.3 — extended).",
  "pass": true
}}
"""


def select_trades(candidates: list[dict], market_context: dict, pool3_context: list[dict],
                  news_context: str = "") -> dict:
    """
    candidates:     scored results from scanner
    market_context: futures, VIX, fear_greed, news
    pool3_context:  real-time pool_filter metrics (vol_ratio, above_vwap, rs_vs_sector)
    news_context:   optional headlines string from news_intel; empty string if not provided
    """
    if not candidates:
        return {"trades": [], "summary": "No candidates passed scanner.", "pass": False}

    # Merge pool3_context into candidates — pool_filter real-time data wins over scanner
    pool3_map = {m["ticker"]: m for m in (pool3_context or [])}
    enriched  = []
    for c in candidates:
        t = c["ticker"]
        m = pool3_map.get(t, {})
        # pool_filter (m) has real-time intraday data: overwrite scanner (c) values for shared keys
        enriched.append({**c, **m})

    now_utc  = datetime.now(timezone.utc)
    now_et   = now_utc + timedelta(hours=-4)  # EDT (UTC-4)

    payload: dict = {
        "date":           now_et.strftime("%Y-%m-%d"),
        "current_time":   now_et.strftime("%H:%M ET"),
        "market_context": market_context,
        "candidates":     enriched,
    }
    if news_context:
        payload["news_context"] = news_context

    user_msg = json.dumps(payload, default=str)

    last_exc = None
    for attempt in range(1, 4):
        try:
            resp = client.messages.create(
                model="claude-opus-4-7",
                max_tokens=2048,
                system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_msg}],
            )
            break
        except (anthropic.APIConnectionError, anthropic.APITimeoutError,
                anthropic.RateLimitError, anthropic.InternalServerError) as exc:
            last_exc = exc
            wait = 15 * attempt
            print(f"  ⚠️  Anthropic API error (attempt {attempt}/3): {exc} — retrying in {wait}s")
            time.sleep(wait)
    else:
        print(f"  ❌ Anthropic API failed after 3 attempts: {last_exc} — skipping trade selection")
        return {"trades": [], "summary": "Claude unavailable — API error.", "pass": False}

    raw  = resp.content[0].text.strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        print(f"[strategy] Unexpected response: {raw[:200]}")
        return {"trades": [], "summary": "Parse error", "pass": False}

    result = json.loads(match.group())
    trades = result.get("trades", [])

    # Stamp strategy tag on every trade
    for t in trades:
        t["strategy"] = STRATEGY_TAG

    print(f"[strategy] Selected {len(trades)} trade(s): {[t['ticker'] for t in trades]}")
    print(f"[strategy] {result.get('summary', '')}")
    return result
