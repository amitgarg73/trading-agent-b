# Product Requirements Document — Trading Agent B
**Version**: 1.0  
**Date**: 2026-05-20  
**Owner**: Amit Garg  
**Status**: Active

---

## 1. Problem Statement

Strategy A (trading-agent) scans 430+ tickers daily. While technically sound, this creates:
- Execution noise from less-liquid names
- Harder-to-model per-stock behavioral patterns
- No feedback loop between results and stock selection

Strategy B tests the hypothesis that **fewer, better-understood stocks trade with higher expectancy** than a broad momentum scan.

---

## 2. Goals

| Goal | Metric |
|------|--------|
| Higher win rate per trade | Strategy B win rate > Strategy A win rate |
| Lower slippage | Avg slippage < Strategy A avg slippage |
| Better fill quality | Fill quality score tracked per trade |
| Self-improving selection | Pool composition changes based on actual P&L |
| Clear comparison | Side-by-side A vs B dashboard |

---

## 3. Non-Goals

- Do NOT touch Strategy A code or infrastructure
- Do NOT share a codebase — separate repo only
- No real money until paper trading validates edge
- No complex ML scoring in Phase 1

---

## 4. Pool System

### Pool 1 — Liquid Universe (~200 stocks)
- Source: S&P 500 filtered by avg daily volume > 5M shares, price > $15
- Updated: Monthly (manual or automated refresh)
- Purpose: Full opportunity set — control group

### Pool 2 — Behavioral Shortlist (25–50 stocks)
- Starts with 25 curated blue chips (see config/blue_chips.py)
- Stocks promoted from Pool 1 when 7-day rolling score > threshold
- Stocks demoted to Pool 1 when 7-day rolling score < threshold
- Scored on: win rate, P&L per trade, slippage, fill quality
- Updated: Daily scoring, monthly composition review

### Pool 3 — Daily Elite Picks (8–10 stocks)
- Filtered each morning from Pool 2 based on real-time conditions:
  - Relative volume > 1.5x own 20-day average
  - Stock moving with or leading its sector ETF
  - No earnings within 2 days
  - Clean technical setup (VWAP, ATR, momentum)
  - Regime check passes
- Updated: Every premarket run

---

## 5. Daily Scoring System

Run at EOD for every Pool 1 and Pool 2 stock that was a candidate or traded:

| Factor | Weight | Scoring |
|--------|--------|---------|
| Win/Loss | 40% | Win = +2, Loss = -1 |
| P&L magnitude | 30% | Scaled to position size |
| Slippage vs expected | 20% | Within 5bps = +1, worse = -1 |
| Setup quality | 10% | Did scanner signals align with outcome |

**Rolling score**: 7-day weighted average (day 7 = 2x weight, older = 1x)

**Promotion threshold**: 7-day score > 6.0 → Pool 1 → Pool 2  
**Demotion threshold**: 7-day score < 2.0 → Pool 2 → Pool 1

---

## 6. Blue Chip Starting List (Pool 2 seed)

### Mag 7
AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA

### Financials
JPM, GS, V, MA, BAC

### Healthcare
UNH, LLY, JNJ

### Energy
XOM, CVX

### Consumer
WMT, COST, HD

### Tech / Semis
AMD, AVGO, NFLX, CRM, ORCL

**Total: 25 stocks** — Pool 2 starts fully seeded with these.

---

## 7. Strategy Logic

Same core approach as Strategy A:
- BUY only (no shorting)
- Intraday, no overnight holds
- Claude agent selects trades from Pool 3 candidates
- Same R:R discipline (3:1 minimum)
- Same position sizing by confidence (HIGH/MEDIUM/LOW)

Key difference: Claude agent receives **behavioral context per stock** (VWAP history, typical ATR range, gap personality) not just today's technical score.

---

## 8. Comparison Framework

Every trade tagged with:
- `strategy = "b"`
- `pool = 1 | 2 | 3`
- `ticker`

Dashboard tracks weekly:
- Win rate by strategy and pool
- Avg P&L per trade by strategy and pool
- Slippage by strategy and pool
- Expectancy (win rate × avg win − loss rate × avg loss)
- Sharpe ratio (rolling 20-day)

---

## 9. Phases

### Phase 1 — Now (Week 1–2)
- Static Pool 2 (25 blue chips, no promotion/demotion yet)
- Daily scoring collected but not yet acting on it
- Basic Streamlit dashboard
- Strategy B running in parallel with A

### Phase 2 — After 30 days of data
- Enable dynamic pool promotion/demotion
- Pool 3 filter tuned based on early results
- Enhanced dashboard with pool comparison

### Phase 3 — After June 8 eval
- Allocate capital based on which pool/strategy is winning
- Tune scoring weights based on 30-day evidence

---

## 10. Success Criteria

- Zero interference with Strategy A
- Strategy B producing comparable or better expectancy within 30 days
- Pool scoring system producing meaningful signal (not random noise)
- Dashboard clearly showing which selection approach wins
