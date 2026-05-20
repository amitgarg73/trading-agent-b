# Design Document — Trading Agent B

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    GitHub Actions                           │
│  trading.yml — premarket / intraday / eod schedule          │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                   orchestrator.py                           │
│                                                             │
│  premarket()  ──▶  pool_filter → scanner → strategy → risk  │
│  intraday()   ──▶  position management → trailing stops     │
│  eod()        ──▶  close positions → pool_scorer → summary  │
└──────┬──────────────────────────────────────────────────────┘
       │
  ┌────┴────────────────────────────────────────────────────┐
  │                                                         │
  ▼                                                         ▼
┌──────────────────┐                        ┌──────────────────────┐
│   scanner/       │                        │   agents/            │
│                  │                        │                      │
│ scanner.py       │                        │ strategy.py          │
│  • behavioral    │                        │  • Claude API        │
│    scoring       │                        │  • blue chip context │
│  • VWAP/ATR/vol  │                        │  • pool-aware prompt │
│                  │                        │                      │
│ pool_filter.py   │                        │ risk.py              │
│  • Pool 2 → 3    │                        │ guardrails.py        │
│  • daily picks   │                        │ alpaca_broker.py     │
└──────────────────┘                        │ pool_scorer.py (EOD) │
                                            └──────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                   core/db.py                                │
│         Supabase client — b_ prefixed tables                │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                   Supabase                                  │
│                                                             │
│  b_pools            — pool membership per stock             │
│  b_stock_scores     — daily scores per stock                │
│  b_trade_plans      — daily trade plans                     │
│  b_planned_trades   — individual planned trades             │
│  b_positions        — open/closed positions                 │
│  b_daily_performance — daily P&L summary                   │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│              Streamlit Dashboard                            │
│                                                             │
│  Page 1: Strategy B                                         │
│    • Pool composition + scores                              │
│    • Today's Pool 3 picks                                   │
│    • P&L by pool                                            │
│    • Position tracker                                       │
│                                                             │
│  Page 2: A vs B Comparison                                  │
│    • Side-by-side P&L                                       │
│    • Win rate comparison                                    │
│    • Expectancy comparison                                  │
│    • Slippage comparison                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## Data Flow — Premarket

```
1. orchestrator.premarket()
2. pool_filter.get_pool3_candidates()
   a. Load Pool 2 stocks from b_pools
   b. Fetch real-time data (yfinance/Alpaca)
   c. Score each on: rel_volume, vwap_position, sector_alignment, earnings_safe
   d. Return top 10–15 candidates for strategy agent
3. scanner.scan(candidates)
   a. Technical scoring (RSI, MACD, BB, volume — same as Strategy A)
   b. Behavioral scoring (VWAP respect, ATR range, momentum)
   c. Returns scored candidate list
4. strategy.select_trades(candidates, market_context)
   a. Claude API call with blue chip context prompt
   b. Returns trade plan (ticker, entry, target, stop, confidence)
5. risk.validate(trade_plan)
6. guardrails.check(trade_plan)
7. alpaca_broker.place_orders(validated_trades) — tagged strategy=b, pool=N
8. db.save_plan(trade_plan) → b_trade_plans, b_planned_trades
```

## Data Flow — EOD + Pool Scoring

```
1. orchestrator.eod()
2. alpaca_broker.close_all_positions()
3. performance.calculate_daily_pnl() → b_daily_performance
4. pool_scorer.score_today()
   a. For each stock that was a candidate or traded today:
      - win/loss, P&L, slippage, setup alignment
   b. Write scores → b_stock_scores
   c. Compute 7-day rolling score per stock
   d. Apply promotions/demotions → update b_pools
5. daily_summary.send()
```

---

## Pool Scoring Schema

```
b_stock_scores
  date         date
  ticker       text
  pool         int        (1, 2, or 3)
  traded       boolean    (was it actually traded?)
  win          boolean    (null if not traded)
  pnl          numeric    (null if not traded)
  slippage_bps numeric    (null if not traded)
  setup_score  numeric    (0-10, based on scanner signals)
  daily_score  numeric    (weighted composite)
  rolling_7d   numeric    (7-day weighted average)
```

---

## Key Design Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Same Supabase project | Yes, b_ prefix | Simplest — one set of credentials, combined dashboard easy |
| Same Alpaca account | Yes, tagged strategy=b | Paper account, no real money distinction needed yet |
| Separate GitHub repo | Yes | Zero risk of touching Strategy A |
| Phase 1: static Pool 2 | Yes | Need 30 days of data before dynamic scoring is meaningful |
| Claude agent kept | Yes | Blue chip context makes it more effective, not less |
| No ML scorer in Phase 1 | Correct | Adds complexity without data to train on |

---

## Shared vs New Components

| Component | Status | Notes |
|-----------|--------|-------|
| Anthropic client | Shared (same key) | Re-implemented, not imported |
| Supabase client | Shared (same credentials) | Re-implemented, b_ table prefix |
| Alpaca broker | New — adapted | strategy_b tag, same account |
| Scanner | New — behavioral focus | VWAP, ATR, volume patterns |
| Strategy agent | New — blue chip prompt | Pool context, stock personality |
| Risk / guardrails | New — adapted | Same logic, b_ table writes |
| Pool filter | Entirely new | Core differentiator |
| Pool scorer | Entirely new | Core differentiator |
| Dashboard | New — Strategy B + combined | Queries both A and B tables |
