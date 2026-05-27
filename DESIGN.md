# Trading Agent B — System Design
**Version:** v2.0 · **Updated:** 2026-05-27

---

## 1. What It Is

An autonomous day-trading system that operates on a curated blue chip universe using a 3-tier behavioral pool system. Rather than scanning 600+ tickers broadly, Strategy B maintains a focused shortlist of stocks that have demonstrated consistent behavioral patterns — predictable VWAP respect, reliable ATR ranges, and repeatable momentum setups.

**Daily objective:** $300–$700 realized P&L via disciplined, high-conviction position management on a focused pool of proven performers.

**Core differentiator vs Strategy A:** Quality over quantity. Every stock Claude sees has been pre-qualified through behavioral scoring. Claude receives per-stock context (rolling 7-day score, VWAP position, sector RS, ATR ratio) that enables more precise confidence assignment.

---

## 2. Architecture

```
cron-job.org (external scheduler)
       │
       ▼
GitHub Actions (trading-agent-b)
  .github/workflows/trading.yml
  premarket · intraday (every 30 min) · EOD
       │
       ▼
orchestrator.py
  premarket()  ──► pool_filter → scanner → news_intel → market_context
                               → strategy → risk → sector_guard
                               → guardrails → alpaca_broker
                               → Supabase (b_ tables)

  intraday()   ──► Guards (slots · runs · loss limit)
                               → intraday_momentum scanner (Pool 3 movers)
                               → strategy → risk → guardrails
                               → alpaca_broker → Supabase

  eod()        ──► close_all_positions
                               → pool_scorer (score + promote/demote)
                               → daily_performance
       │
       ▼
┌────────────────────┐     ┌──────────────────────┐
│  scanner/          │     │  agents/              │
│  scanner.py        │     │  strategy.py          │
│  pool_filter.py    │     │  risk.py              │
│  intraday_         │     │  guardrails.py        │
│  momentum.py       │     │  news_intel.py        │
└────────────────────┘     │  market_context.py   │
                           │  pool_scorer.py (EOD)│
                           │  alpaca_broker.py    │
                           └──────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────┐
│  core/db.py — Supabase client (b_ prefixed tables)│
│                                                  │
│  b_pools             pool membership per stock   │
│  b_stock_scores      daily behavioral scores     │
│  b_trade_plans       daily trade plans           │
│  b_planned_trades    individual planned trades   │
│  b_positions         open/closed positions       │
│  b_daily_runs        one row per scan event      │
│  b_daily_performance daily P&L summary           │
└──────────────────────────────────────────────────┘
       │
       ▼
Streamlit Dashboard
  Strategy B tab: pool composition, Pool 3 picks, P&L by pool
```

**Stack:** Python 3.11 · Claude claude-opus-4-7 · Alpaca Markets API · Supabase (PostgreSQL) · Streamlit Cloud · GitHub Actions

---

## 3. Pool System

The 3-tier pool is Strategy B's core differentiator. Every stock must earn its place through demonstrated behavioral consistency before Claude ever sees it.

### Pool 1 — Full Blue Chip Universe (~150 stocks)

The complete eligible universe of large-cap, liquid stocks. These are well-known names with significant daily volume, tight spreads, and predictable institutional behavior. Stocks enter Pool 1 at system initialization and are re-seeded from `POOL_2_SEED` config if the table is empty.

**Characteristics:** Market cap > $10B, avg volume > 2M shares/day, liquid enough for bracket orders without significant slippage.

**Movement:** Stocks demoted from Pool 2 return here. Stocks promoted from Pool 1 move to Pool 2 (manual or score-triggered).

---

### Pool 2 — Behavioral Shortlist (~25–50 stocks)

Stocks that have demonstrated consistent, repeatable behavioral patterns for this strategy over rolling 7-day windows. This is the active working set — Pool Filter selects Pool 3 exclusively from Pool 2.

**Selection criteria for Pool 2:**
- Consistent VWAP respect (price bounces off VWAP with low slippage)
- ATR moves that align with the +2% target without excessive overshoot
- Volume patterns that confirm momentum (volume surge at breakout)
- Sector relative strength that supports directional bias

**Promotion/demotion:** Pool Scorer runs EOD. Stocks in Pool 3 with `rolling_score ≥ 6` over 7 days are promoted to Pool 2. Stocks with `rolling_score < 3` are demoted to Pool 1.

**Phase 1 note:** Pool 2 is seeded statically from `POOL_2_SEED` config on first run. Dynamic promotion from Pool 1 requires 30+ days of data to be meaningful.

---

### Pool 3 — Daily Elite Picks (8–10 stocks)

Selected fresh each morning by Pool Filter from Pool 2. These are the only stocks Claude can trade on any given day. Pool 3 is ephemeral — it exists for the trading day and is re-derived each morning.

**Selection signals (Pool Filter scores each Pool 2 stock):**
- `vwap_position`: Is price above VWAP at market open? (high weight)
- `orb_breakout`: Did price break above Opening Range (9:30–9:45 AM high)?
- `volume_ratio`: Current volume vs 20-day average (surge = bullish confirmation) — uses 30-day history so `tail(20)` is a genuine 20-day average
- `sector_rs`: Sector relative strength vs SPY for the day — only computed when sector move is >0.3% to avoid flat-day noise amplification
- `volume_acceleration`: Rate of volume build in first 30 minutes
- `market_rs`: Relative strength vs SPY directly (in addition to sector RS)

**Quality floor:** Before selecting top N, tickers must pass `filter_score > POOL3_MIN_FILTER_SCORE` (default 0.0). This removes stocks with net-negative signals even if they rank in the top 8–10 by relative score on a weak day.

**Pool 2 seed demotion:** Stocks in the `POOL_2_SEED` config can now be demoted if their `rolling_score` falls below the demotion threshold. Previously seed stocks were immune to demotion, which allowed underperformers to persist indefinitely.

Top 8–10 passing stocks become Pool 3 for the day. These candidates are passed to the Scanner for behavioral scoring, then to Claude with full per-stock context.

---

## 4. Agent Inventory

| Agent | File | Inputs | Outputs | Responsibility |
|-------|------|--------|---------|----------------|
| **Pool Manager** | `core/pool_manager.py` | `POOL_2_SEED` config, `b_pools` table | Updated `b_pools` | Maintains Pool 1/2/3 membership in Supabase. Seeds pools from config if empty. Called at startup. |
| **Pool Filter** | `scanner/pool_filter.py` | Pool 2 stocks from `b_pools`, real-time Alpaca/yfinance data | Pool 3 candidate list (8–10 stocks) with VWAP/ORB/volume/sector scores | Each morning: fetches live data for all Pool 2 stocks, scores on 5 signals, returns top picks as Pool 3 daily elite. |
| **Scanner** | `scanner/scanner.py` | Pool 3 candidates | Scored candidate list with RSI, MACD, BB, volume, VWAP respect, ATR ratio, breakout freshness | Behavioral scoring: VWAP respect, ATR range alignment, volume patterns, RSI, MACD, SMA. Breakout freshness: +1 score if price 0–5% above SMA20 (FRESH), −1 if >12% above (EXTENDED). RS flat-day guard: sector return must be >0.3% for RS signal to fire. |
| **News Intel** | `agents/news_intel.py` | Scored candidates | Filtered candidates + news context | Earnings blackout filter. Removes earnings-day tickers. Adds news sentiment context to surviving candidates. |
| **Market Context** | `agents/market_context.py` | VIX, Fear & Greed API, yfinance data | `market_context` dict with flags and sector rotation | VIX thresholds, Fear & Greed, SPY futures bias, economic calendar. Sector rotation: 11 sector ETFs (XLK–XLU) ranked by today's return; top leaders and laggards included in Claude's market summary. |
| **Strategy Agent** | `agents/strategy.py` | Enriched candidates, market context, per-stock behavioral data | Trade plan (ticker, entry, target, stop, confidence, reasoning) | Claude claude-opus-4-7. Receives `pool`, `rolling_score`, `above_vwap`, `rs_vs_sector`, `atr_ratio`, `signal_type` per stock. Applies time-of-day rules. INTRADAY_MOMENTUM candidates bypass 1pm pool restriction. Prompt cached. |
| **Risk Agent** | `agents/risk.py` | Trade plan | Validated/rejected trades | Validates R:R ≥ 2.0, position size within bounds (`$2,500–$3,500`), stop not too wide, max loss per trade check. |
| **Sector Guard** | `agents/sector_guard.py` | Validated trades, current positions | Filtered trades | Sector concentration cap. Uses `SECTOR_MAP` + yfinance fallback. |
| **ATR Sizer** | `agents/atr_sizer.py` | Sector-guard-approved trades, scanner ATR map | ATR-adjusted trades or dropped list | P0-1: stop = max(ATR × 1.2, 0.5%); shares from constant $150 risk, capped at confidence limit. P0-2: ORB gate — if first-30-min range < 0.5 × ATR, halve shares. Drops trades where ATR stop ≥ target (R:R < 1). |
| **Guardrails** | `agents/guardrails.py` | Trade list, daily P&L state | Final approved trades | Safety checks: no duplicates, price sanity (>5% from market = reject), daily loss limit, capital cap. Accepts ATR-based stops via `atr_stop_pct` field; verifies stop matches ATR formula rather than fixed 0.67% formula. |
| **Pool Scorer** | `agents/pool_scorer.py` | Today's Pool 3 trades from `b_positions`, `b_planned_trades` | Updated `b_stock_scores`, updated `b_pools` | EOD: scores each Pool 3 stock on win/loss, P&L, slippage, setup alignment. Computes 7-day rolling score. Promotes stocks with `rolling_score ≥ 6`; demotes stocks with `rolling_score < 3`. |
| **Intraday Momentum Scanner** | `scanner/intraday_momentum.py` | Pool 3 stocks, SPY snapshot, Alpaca live prices | Momentum candidates with `pool=2` and `signal_type=INTRADAY_MOMENTUM` | SPY gate (≥+0.5%). Scans Pool 3 for stocks up ≥0.5% above VWAP. Max 6 runs/day, min 90 min between runs. Returns candidates with `pool` field for time-of-day classification. |
| **Alpaca Broker** | `agents/alpaca_broker.py` | Validated trades | Bracket order confirmations, live prices, snapshots | Alpaca API wrapper. Bracket orders tagged `strategy=b`. `get_orders`, place bracket orders, get snapshots, live prices. |

---

## 5. Daily Pipeline

### 5.1 Premarket — 10:00 AM ET

Runs once before significant intraday volume develops. Pool Filter runs first to derive the day's Pool 3, then the full pipeline runs on those stocks only.

| Step | Agent | What Happens |
|------|-------|-------------|
| **0. Pool Setup** | Pool Manager | Verifies pool membership is current. Seeds from config if `b_pools` is empty. |
| **1. Pool Filter** | Pool Filter | Fetches real-time data for all Pool 2 stocks. Scores on VWAP position, ORB breakout, volume ratio, sector RS, volume acceleration. Returns top 8–10 as Pool 3. |
| **2. Behavioral Scan** | Scanner | Scores Pool 3 stocks: VWAP respect, ATR alignment, volume patterns, RSI, MACD, SMA20/50. Returns `behavioral_score` per stock. |
| **3. News Filter** | News Intel | Removes earnings-day tickers. Adds news sentiment context to market summary. |
| **4. Market Context** | Market Context | Fetches VIX, Fear & Greed, US futures, economic calendar, and sector rotation (11 ETFs). Sets `max_positions` and `quiet_day`. Hard skip if futures < −1.5%. |
| **4.5 SPY Gate** | orchestrator | Checks SPY `today_pct_change`. If negative, skips the day entirely (harder gate than Strategy A which only reduces max_positions). |
| **4.6 Candidate Cap** | orchestrator | Sorts candidates by technical score descending; retains top `MAX_DAILY_ENTRIES` (10) before the Claude call. |
| **5. Strategy (Claude)** | Strategy Agent | claude-opus-4-7 receives Pool 3 candidates with full behavioral context. Selects trades, assigns confidence, sets entry/target/stop using fixed formulas. Writes reasoning. |
| **6. Risk Validation** | Risk Agent | Enforces R:R ≥ 2.0, position size bounds, max loss per trade. |
| **7. Sector Guard** | Sector Guard | Caps exposure at sector concentration limit. |
| **7.5 ATR Sizer (P0)** | ATR Sizer | Replaces formula stop with ATR-based stop (`max(ATR × 1.2, 0.5%)`). Shares from $150 constant risk. ORB gate: halves shares on choppy opens. Drops trades where stop ≥ target. |
| **8. Guardrails** | Guardrails | Blocks duplicates, price sanity check, daily loss limit. Accepts `atr_stop_pct` field for wide-stop validation. |
| **9. Execute** | Alpaca Broker | Places bracket orders tagged `strategy=b`. Records positions in `b_positions`. |

### 5.2 Intraday — Every 30 min, 10:00 AM–3:45 PM ET

**Position Management (every cycle):**
- Reconcile: detect positions closed by Alpaca bracket (stop/target fired), record real exit price and P&L
- Refresh: sync current price and unrealized P&L for open positions
- Lock-in logic: Tier 1 ($500 realized) — let winners ride; Tier 2 ($700 total) — close everything

**Momentum Scan (conditional, max 6/day, min 90 min apart):**
- **Orchestrator SPY gate:** SPY `today_pct_change` must be >= `MIN_SPY_MOVE_PCT` (0.3%) to proceed — checked before momentum scanner runs.
- **Momentum scanner SPY gate:** SPY must be up ≥+0.5% (existing, stricter secondary check inside `intraday_momentum.py`)
- Scan Pool 3 for stocks up ≥+0.5% above VWAP
- Candidates tagged with `pool=2` and `signal_type=INTRADAY_MOMENTUM`
- Run through Strategy → Risk → Guardrails → Execute pipeline
- Max intraday target is +1% (shorter window to close, tighter target)
- INTRADAY_MOMENTUM signal bypasses the 1pm pool restriction

**Guards checked before momentum scan:**
1. Max concurrent positions already at limit? → Skip
2. Max daily runs (6) already reached? → Skip
3. Minimum 90 min since last run? → Skip if not met
4. Daily loss limit breached? → Skip

### 5.3 EOD — Post 4:00 PM ET

| Step | What Happens |
|------|-------------|
| **Dedup Guard** | Checks `b_scan_results` for a `run_eod_started` record for today. If found, exits immediately — prevents double-runs when GitHub Actions fires twice. |
| **Start Log** | `_log_run_b("eod", "started")` writes a record to `b_scan_results`. Subsequent crash or completion updates the same record to `failed`/`completed`. |
| **Close Positions** | Market-sell all remaining open positions. Cancel pending bracket legs. |
| **Alert on Unclosed** | If any positions remain open after close attempt, `send_alert()` fires a Gmail SMTP alert. |
| **Pool Scoring** | Pool Scorer evaluates each Pool 3 stock: win/loss, P&L, slippage vs ATR, setup alignment score. Writes to `b_stock_scores`. Computes 7-day rolling score. Promotes/demotes stocks between Pool 1 and Pool 2. Both `score_today()` and `write_daily_performance()` are wrapped in `try/except` — failures print a warning but do not crash EOD. |
| **Daily Performance** | Writes P&L summary to `b_daily_performance`: realized P&L, win rate, position count, best/worst trade, plus `friction_breakdown` (entry slippage bps), `alpaca_equity`, `friction_gap`, `vix_level`, `fear_greed`, `spy_change_pct`, `regime_label`. |

---

## 6. Full Agent Workflow Diagram

```
═══════════════════════════════════════════════════════════════════════════
PREMARKET (10:00 AM ET)
═══════════════════════════════════════════════════════════════════════════

  cron-job.org  ──►  GitHub Actions  ──►  orchestrator.premarket()
                                                    │
                                                    ▼
                                         ┌─────────────────────┐
                                         │    Pool Manager      │
                                         │  Verify b_pools      │
                                         │  Seed if empty       │
                                         └──────────┬──────────┘
                                                    │ Pool 1+2 membership
                                                    ▼
                                         ┌─────────────────────┐
                                         │    Pool Filter       │
                                         │  Pool 2 → Pool 3     │
                                         │  Score 5 signals     │
                                         │  Return top 8–10     │
                                         └──────────┬──────────┘
                                                    │ Pool 3 candidates
                                              ┌─────┴──────┐
                                              │            │
                                              ▼            ▼
                              ┌──────────────────┐  ┌─────────────────┐
                              │    Scanner        │  │  Market Context  │
                              │ Behavioral score  │  │ VIX · F&G        │
                              │ VWAP · ATR · Vol  │  │ Futures gate     │
                              └───────┬──────────┘  └───────┬─────────┘
                                      │                      │
                                      ▼                      ▼
                              ┌──────────────────┐  ┌─────────────────┐
                              │   News Intel      │  │ SKIP?           │◄─── futures < -1.5%
                              │ Earnings blackout │  │ YES → halt      │
                              │ News sentiment    │  │ NO  → continue  │
                              └───────┬──────────┘  └────────┬────────┘
                                      │ enriched candidates  │ market_context
                                      └──────────┬───────────┘
                                                 │
                                                 ▼
                                      ┌───────────────────────┐
                                      │   Strategy Agent       │
                                      │   Claude claude-opus-4-7│
                                      │                        │
                                      │ Reads per-stock:       │
                                      │  pool (1/2/3)          │
                                      │  rolling_score (0-10)  │
                                      │  above_vwap (bool)     │
                                      │  rs_vs_sector (ratio)  │
                                      │  atr_ratio (float)     │
                                      │  signal_type           │
                                      │                        │
                                      │ Assigns: HIGH/MED/LOW  │
                                      │ Sets: entry/target/stop│
                                      │ Writes: reasoning      │
                                      └───────────┬────────────┘
                                                  │ trade_plan
                                                  ▼
                                      ┌───────────────────────┐
                                      │     Risk Agent         │
                                      │  R:R ≥ 2.0            │
                                      │  Size: $2500–$3500     │
                                      │  Stop ≤ 0.67%         │
                                      └───────────┬────────────┘
                                                  │
                                              ┌───┴────────┐
                                              │            │
                                              ▼            ▼
                                    ┌──────────────┐ ┌───────────────┐
                                    │ Sector Guard  │ │  Guardrails   │
                                    │ Sector cap    │ │ Duplicates    │
                                    └──────┬───────┘ │ Price sanity  │
                                           │         │ Loss limit    │
                                           └────┬────┘
                                                │ validated trades
                                                ▼
                                      ┌───────────────────────┐
                                      │    Alpaca Broker       │
                                      │ Place bracket orders   │
                                      │ Tag: strategy=b        │
                                      │ Leg A: half, +1% tgt   │
                                      │ Leg B: rest, +2% tgt   │
                                      │ Both: -0.67% stop      │
                                      └───────────┬────────────┘
                                                  │
                                                  ▼
                                      ┌───────────────────────┐
                                      │       Supabase         │
                                      │ b_trade_plans          │
                                      │ b_planned_trades       │
                                      │ b_positions            │
                                      │ b_daily_runs (run=0)   │
                                      └───────────────────────┘


═══════════════════════════════════════════════════════════════════════════
INTRADAY (every 30 min · 10:00 AM–3:45 PM ET · momentum scan conditional)
═══════════════════════════════════════════════════════════════════════════

  Every 30 min:
  ┌────────────────────────────────────────────────────────────────┐
  │  Position Management                                           │
  │                                                                │
  │  Reconcile ──► Detect bracket exits (stop/target/UNFILLED)    │
  │  Refresh   ──► Sync price, unrealized P&L, high watermark     │
  │  Lock-in   ──► Tier 1 $500: tighten trail                     │
  │                Tier 2 $700: close all                         │
  └────────────────────────────────────────────────────────────────┘
                              │
  Momentum scan guards:       │
  ┌──────────────┐            ▼
  │ max_runs=6?  │──YES──► Skip
  │ min_90min?   │──NO ──►
  │ slots open?  │
  │ loss_limit?  │
  └──────┬───────┘
         │ all guards pass
         ▼
  ┌─────────────────────────────┐
  │  Intraday Momentum Scanner  │
  │  SPY gate: SPY ≥ +0.5%      │──NO──► Skip
  │  Pool 3: price ≥ +0.5%      │
  │         above VWAP          │
  │  Returns: pool=2,           │
  │   signal_type=              │
  │   INTRADAY_MOMENTUM         │
  └─────────────┬───────────────┘
                │ momentum candidates
                ▼
  Strategy Agent → Risk Agent → Guardrails → Alpaca Broker
  (same pipeline as premarket, target capped at +1%)

  Note: INTRADAY_MOMENTUM candidates bypass 1pm pool restriction
  because they are confirmed movers, not speculative setups.


═══════════════════════════════════════════════════════════════════════════
EOD (post 4:00 PM ET)
═══════════════════════════════════════════════════════════════════════════

  Close All Positions
         │
         ▼
  ┌──────────────────────────────────────────────┐
  │  Pool Scorer                                  │
  │                                              │
  │  For each Pool 3 stock today:                │
  │    win_loss     (1 if P&L > 0 else 0)        │
  │    pnl          (realized P&L in $)          │
  │    slippage_bps (vs ATR-expected range)      │
  │    setup_score  (scanner signal alignment)   │
  │    daily_score  (weighted composite 0-10)    │
  │                                              │
  │  7-day rolling_score = weighted avg          │
  │                                              │
  │  rolling_score ≥ 6 → Promote to Pool 2       │
  │  rolling_score < 3 → Demote to Pool 1        │
  │  3 ≤ score < 6    → Stay in current pool     │
  └──────────────┬───────────────────────────────┘
                 │
        ┌────────┴────────┐
        ▼                 ▼
  b_stock_scores      b_pools (updated)
        │
        ▼
  b_daily_performance (P&L summary)
```

---

## 7. Trading Logic

### 7.1 Position Sizing

| Confidence | Size | Assignment Criteria |
|------------|------|---------------------|
| HIGH | $3,500 | `rolling_score ≥ 7` AND `above_vwap=True` AND `rs_vs_sector ≥ 1.5` |
| MEDIUM | $3,000 | `rolling_score 4–6` OR (`rolling_score 3–4` AND `above_vwap=True` AND `rs_vs_sector ≥ 1.2`) |
| LOW | $2,500 | `rolling_score 3–4`, weaker VWAP/RS signals |

Confidence is assigned by Claude based on behavioral context, VWAP position, and sector relative strength. The `rolling_score` is the primary differentiator vs Strategy A — it encodes historical behavioral fit of this specific stock with this strategy.

### 7.2 Trade Formulas (Hard Rules)

```
entry_price    = Alpaca ask price (live) or scanner close
target_price   = round(entry × 1.04, 2)     # +4% ceiling — limit order on Leg B
partial_target = round(entry × 1.005, 2)    # +0.5% partial exit (Leg A)

# ATR-based stop (P0) — applied by atr_sizer.py after sector guard:
stop_pct    = max(atr_pct × 1.2, 0.5%)     # outside the noise band; floor 0.5%
stop_loss   = round(entry × (1 − stop_pct), 2)
shares      = min(int($150 / (entry × stop_pct)), int(position_size_cap / entry))

Reward:Risk (ceiling)    = 4.00% / stop_pct (varies; typically 1.5:1–6.0:1)
Reward:Risk (intraday)   = 1.00% / stop_pct
```

**Ceiling vs trail:** Native trailing stop (1% from peak) handles most exits between +0.5% and +3.9%. The 4% ceiling limit order only fires on straight-line momentum runs with no 1% pullback — the days you want maximum capture. Raised from 2.5% to avoid prematurely capping strong momentum days.

**Intraday cap:** Momentum entries use +1% target (not +4%) because time remaining to EOD is shorter. Guardrails accepts either `TARGET_PCT` (4%) or `INTRADAY_TARGET_PCT` (1%) to correctly validate both entry types.

### 7.3 Partial Profit Design

Each trade (premarket) opens as two independent bracket orders:

```
Leg A  →  shares // 2   ·  target = entry × 1.005  (+0.5%)
Leg B  →  shares − A    ·  target = entry × 1.02   (+2%)
Both   →  stop_loss = entry × 0.9933
```

**Why:** Converts all-or-nothing bracket outcomes into graduated P&L. 0.5% moves happen more frequently than 1% moves — Leg A closes early, reducing full stop-out frequency. Leg B continues trailing toward the +2% target.

### 7.4 Time-of-Day Rules

| Time | Rule | Rationale |
|------|------|-----------|
| 10:00–12:59 PM | New entries allowed from Pool 2/3 stocks | Full day ahead, worth the risk |
| 1:00–3:45 PM | Pool restriction: only Pool 3 stocks, no new Pool 2 entries | Less time to recover from a bad entry |
| 3:45 PM | No new entries, manage existing only | Too close to close |
| `signal_type=INTRADAY_MOMENTUM` | **Exempt from 1pm restriction at any hour** | Confirmed movers, not speculative setups |

**INTRADAY_MOMENTUM exception:** When the intraday momentum scanner returns a candidate with `signal_type=INTRADAY_MOMENTUM`, the strategy prompt explicitly exempts it from the pool 1pm restriction. These are stocks already up ≥0.5% above VWAP with SPY confirmation — they are validated momentum plays, not speculative setups. The `pool=2` field is included so Claude can apply time-of-day classification logic correctly while recognizing the exception.

### 7.5 Trailing Stop

Manual high-watermark trail checked every 30 min:

```
effective_stop = max(stop_loss, high_watermark × (1 − 1.0%))

After Tier 1 lock-in ($500 realized):
effective_stop = max(stop_loss, high_watermark × (1 − 0.5%))
```

### 7.6 Entry Limit Pricing

At execution time, `hybrid_limit_price(ask, bid)` sets the bracket order limit price. Passive-first — the stock must come to us.

| Spread | Limit Price | Rationale |
|--------|-------------|-----------|
| < 0.10% of ask | bid | Ultra-tight market; fills on any normal tick |
| 0.10–0.20% | mid | Moderate spread; mid fills on normal intraday dips |
| > 0.20% | skip (None) | Wide spread destroys R:R before entry |

Same logic as Strategy A. Tradeoff: better average entry price vs higher unfill rate on straight-line days.

---

## 8. Risk Controls

Six independent layers applied in sequence — any one can block a trade:

| Layer | Agent | What It Blocks |
|-------|-------|----------------|
| **Market Gate** | Market Context | Trading on crash days (futures < −1.5%), extreme volatility |
| **News Filter** | News Intel | Earnings-day tickers, negative catalyst stocks |
| **Risk Agent** | risk.py | R:R below 2.0 floor, position size out of bounds, stop too wide; daily loss limit checked as MTM (realized + unrealized) so open positions bleeding losses fire the limit before new trades are entered |
| **Sector Guard** | sector_guard.py | Sector concentration breaches |
| **ATR Sizer** | atr_sizer.py | Drops trades where ATR stop ≥ target; halves shares on choppy opens (ORB < 0.5 × ATR) |
| **Guardrails** | guardrails.py | Duplicates, price sanity (>5% from market), daily loss limit, max positions. `_traded_today()` uses a date-filtered query so only today's positions are loaded from Supabase. |
| **API Resilience** | strategy.py | Anthropic API failures: 3-attempt retry with 15/30/45s backoff; on total failure returns empty trades (graceful skip, no crash) |

### 8.1 Intraday Momentum Guards

Additional guards before each momentum scan run:

| Guard | Threshold | Reason |
|-------|-----------|--------|
| Max daily runs | 6 | Prevents overtrading on volatile days |
| Min interval | 90 min | Ensures genuine new signal, not noise |
| SPY gate | ≥+0.5% | Confirms broad market support for momentum |
| Open slots | < max_positions | Must have capacity before scanning |
| Loss limit | Daily P&L > limit | Don't compound losses with momentum trades |

### 8.2 Pool Behavioral Gate

The Pool Filter itself is a risk layer — only stocks with proven behavioral track records enter Pool 3. A stock that consistently stops out, shows wide slippage, or fails to hit targets gets demoted via the Pool Scorer. This pre-screens away "technically valid but behaviorally unreliable" setups before they ever reach Claude.

---

## 9. Key Configuration

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `TOTAL_CAPITAL` | $50,000 | Simulated account size |
| `TARGET_PCT` | 4.0% | Ceiling limit order on Leg B — trail exits earlier in most trades |
| `INTRADAY_TARGET_PCT` | 1.0% | Intraday entry profit target |
| `PARTIAL_PROFIT_PCT` | 0.5% | Partial exit (Leg A) — lowered from 1% to capture profit before reversals |
| `MAX_LOSS_PER_TRADE` | 0.67% | Formula stop (Claude prompt reference; overridden by ATR sizer at runtime) |
| `ATR_STOP_MULTIPLIER` | 1.2 | ATR-based stop multiplier: stop = max(ATR × 1.2, 0.5%) |
| `ATR_STOP_FLOOR` | 0.5% | Minimum stop regardless of ATR |
| `MAX_LOSS_DOLLARS` | $150 | Constant dollar risk per trade |
| `ORB_ATR_FLOOR` | 0.5 | ORB/ATR ratio below which open is choppy → halve shares |
| `MIN_REWARD_RISK` | 2.0 | R:R floor (both premarket and intraday) |
| `TRAIL_PCT` | 1.0% | Trailing stop from high watermark |
| `LOCK_IN_TRAIL_PCT` | 0.5% | Tighter trail after Tier 1 |
| `DAILY_LOCK_IN_TARGET` | $500 | Tier 1: let winners ride |
| `DAILY_BONUS_TARGET` | $700 | Tier 2: close everything |
| `DAILY_LOSS_LIMIT` | −$250 | Stop trading for the day (0.5% of capital) |
| `MAX_POSITIONS` | 10 | Max concurrent positions |
| `MAX_INTRADAY_RUNS` | 6 | Max momentum scan runs per day |
| `MIN_INTRADAY_INTERVAL_MIN` | 90 | Min minutes between momentum scans |
| `MIN_SPY_MOVE_PCT` | 0.3% | Orchestrator-level SPY gate — skips day entirely if SPY negative at premarket; blocks intraday scan if SPY < +0.3% |
| `MAX_DAILY_ENTRIES` | 10 | Hard cap on total new positions per calendar day |
| `SPY_MOMENTUM_GATE` | +0.5% | Momentum scanner SPY gate (secondary, stricter than orchestrator gate) |
| `POOL_PROMOTE_THRESHOLD` | 6.0 | 7-day rolling score to promote to Pool 2 |
| `POOL_DEMOTE_THRESHOLD` | 3.0 | 7-day rolling score to demote from Pool 2 (applies to seed stocks too) |
| `POOL3_MIN_FILTER_SCORE` | 0.0 | Quality floor: Pool Filter rejects tickers with net-negative composite score |
| `HIGH_CONFIDENCE_SIZE` | $3,500 | HIGH confidence position size |
| `MEDIUM_CONFIDENCE_SIZE` | $3,000 | MEDIUM confidence position size |
| `LOW_CONFIDENCE_SIZE` | $2,500 | LOW confidence position size |

---

## 10. Data Model — Supabase Tables

All tables use `b_` prefix to share the same Supabase project as Strategy A.

### b_pools

```sql
id              uuid        primary key
ticker          text        not null
pool            int         not null    -- 1, 2, or 3
added_date      date
rolling_score   numeric     -- current 7-day weighted average
last_traded     date
notes           text        -- manual annotation
```

### b_stock_scores

```sql
id              uuid        primary key
date            date        not null
ticker          text        not null
pool            int         -- pool at time of scoring
traded          boolean     -- was it actually traded today?
win             boolean     -- null if not traded
pnl             numeric     -- null if not traded
slippage_bps    numeric     -- realized slippage in basis points
setup_score     numeric     -- 0-10, scanner signal alignment
daily_score     numeric     -- weighted composite for this day
rolling_7d      numeric     -- 7-day weighted average
```

### b_trade_plans

```sql
id              uuid        primary key
date            date        not null
run_id          uuid        references b_daily_runs(id)
market_context  jsonb       -- VIX, F&G, futures snapshot
pool3_tickers   text[]      -- which stocks were Pool 3 today
plan_json       jsonb       -- full Claude response
created_at      timestamptz
```

### b_planned_trades

```sql
id              uuid        primary key
date            date        not null
trade_plan_id   uuid        references b_trade_plans(id)
ticker          text        not null
confidence      text        -- HIGH / MEDIUM / LOW
pool            int
rolling_score   numeric
signal_type     text        -- PREMARKET / INTRADAY_MOMENTUM
entry_price     numeric
target_price    numeric
stop_price      numeric
position_size   numeric
shares          int
reasoning       text
status          text        -- PLANNED / EXECUTED / SKIPPED / REJECTED
```

### b_positions

```sql
id              uuid        primary key
date            date        not null
run_id          uuid        references b_daily_runs(id)
ticker          text        not null
status          text        -- OPEN / CLOSED / UNFILLED / STOP / TARGET
pool            int
signal_type     text
entry_price     numeric
exit_price      numeric
shares          int
position_size   numeric
realized_pnl    numeric
unrealized_pnl  numeric
high_watermark  numeric
close_reason    text        -- TARGET / STOP / TRAIL / LOCK_IN / EOD
exit_mechanism  text        -- BRACKET / MANUAL / RECONCILE
entry_time      timestamptz
exit_time       timestamptz
alpaca_order_id text
```

### b_daily_runs

```sql
id              uuid        primary key
date            date        not null
run_number      int         -- 0=premarket, 1-6=intraday momentum runs
run_type        text        -- PREMARKET / INTRADAY_MOMENTUM
started_at      timestamptz
completed_at    timestamptz
pool3_tickers   text[]
candidates_count int
trades_placed   int
skipped_reason  text        -- null if run completed normally
```

### b_daily_performance

```sql
id              uuid        primary key
date            date        not null
realized_pnl    numeric
unrealized_pnl  numeric
total_pnl       numeric
win_count       int
loss_count      int
win_rate        numeric
best_trade_pnl  numeric
worst_trade_pnl numeric
positions_count int
pool2_pnl       numeric     -- P&L from Pool 2 stocks specifically
pool3_pnl       numeric
momentum_pnl    numeric     -- P&L from INTRADAY_MOMENTUM entries
notes           text
```

---

## 11. Key Design Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| **Claude model** | claude-opus-4-7 (not Sonnet) | Blue chip behavioral context is more nuanced than broad scanning. Higher quality reasoning justifies cost on a 10-stock universe. |
| **3-tier pool** | Pool 1 → 2 → 3 | Behavioral pre-qualification reduces the burden on Claude. Each tier filters for fit, not just technicals. |
| **Static Pool 2 seed** | Yes, Phase 1 | Need 30+ trading days of data before Pool Scorer has enough signal to drive dynamic Pool 1→2 promotion meaningfully. |
| **No ML scorer** | Correct for Phase 1 | Adds complexity without data to train on. Pool behavioral scoring is the equivalent signal. |
| **Same Supabase project** | Yes, b_ prefix | One set of credentials, combined dashboard. Zero infra overhead. |
| **Same Alpaca account** | Yes, strategy=b tag | Paper account — no real money distinction needed. Tag enables per-strategy P&L reporting. |
| **Separate GitHub repo** | Yes | Zero risk of touching Strategy A code. Independent deployment, independent failure modes. |
| **R:R floor = 2.0** | Lower than A's 3.0 | Pool 3 stocks have proven behavioral fit — higher hit rate justifies accepting slightly lower R:R. 2:1 at 75%+ win rate is +EV. |
| **Intraday target = +1%** | Capped below premarket | Shorter window means less time for a +2% move to develop. +1% target with -0.67% stop gives 1.5:1 R:R; paired with momentum confirmation this is still +EV at high win rates. |
| **INTRADAY_MOMENTUM pool field** | Required in scanner output | Strategy prompt must classify candidates by signal type to apply time-of-day rules correctly. Without the `pool` field, Claude cannot distinguish intraday momentum from regular pool entries. |

---

## 12. Change Log

### v2.0 — 2026-05-27

**Win-rate fixes + passive entry pricing (mirroring Strategy A v5.26)**

- `PARTIAL_PROFIT_PCT` 1%→0.5%: Leg A now targets +0.5% so profit is captured before the first potential reversal. More legs close at a gain; full stop-outs happen less often.
- `MAX_DAILY_ENTRIES=10` added: hard cap on daily new positions, same logic as Strategy A.
- SPY premarket gate (step 4.5): if SPY `today_pct_change` < 0, skip the day entirely. Harder than Strategy A (which only reduces max_positions by 3) — Strategy B's small Pool 3 universe is more exposed on down-SPY days.
- SPY intraday gate (step 4.5 orchestrator-level, `MIN_SPY_MOVE_PCT=0.3%`): added upstream of the existing momentum scanner's +0.5% gate. Now two checks: orchestrator blocks below 0.3%, scanner blocks below 0.5%.
- Premarket candidate cap (step 4.6): top 10 by technical score before Claude call.
- `hybrid_limit_price` passive-first: bid on tight spread (<0.1%), mid on moderate (0.1–0.2%), None on wide. Stock must come to us, not us chasing the ask.
- Tests: 342 passing.

### v1.9 — 2026-05-26

**Ported bug fixes from Strategy A (audit pass)**

- **CLEANUP/UNFILLED exclusion:** `_today_realized_pnl()` in `orchestrator.py`, `risk.py`, and `alpaca_broker.py` now exclude positions with `close_reason` of CLEANUP or UNFILLED from the daily realized total. Previously all closed positions were summed, allowing phantom P&L to distort the daily loss limit and bonus target checks.
- **Date-filtered DB queries:** All 4 historical CLOSED-position queries now push a date-range filter to Supabase via `filters_gte`/`filters_lte` instead of loading the full table and filtering in Python. `core/db.py` `select()` gains `filters_gte` and `filters_lte` parameters. Affected locations: `orchestrator.py` realized P&L, `orchestrator.py` traded-today set, `alpaca_broker.py` bonus target check, `guardrails.py` `_traded_today()`.
- Tests: 313 passing.

### v1.8 — 2026-05-23

**Structural gap fixes (Gaps 4, 5, 6, 7, 8)**

- **Gap 4 — MTM loss limit** (`agents/risk.py`): `validate()` now checks MTM P&L (realized + unrealized) via `_today_net_pnl(open_pos)`. Open positions bleeding losses now fire the daily loss limit before new trades are entered.
- **Gap 5 — EOD dedup guard** (`orchestrator.py`): `eod()` checks `b_scan_results` for a `run_eod_started` record before proceeding; exits early on duplicate. Intraday guard also skips if no `b_trade_plans` row exists for today.
- **Gap 6 — Run observability** (`core/alerts.py` added): Gmail SMTP alert module. `_log_run_b()` writes start/complete/failed records to `b_scan_results`. EOD sends alert on crash or unclosed positions after market close.
- **Gap 7 — Eval framework** (`eval_b.py` created): `_compute_metrics()`, `_gate_check()`, `run_eval()`. Pass criteria: avg P&L ≥ $500, win day rate ≥ 80%, trade win rate ≥ 60%, grade A, no integrity flags. CLI exits 1 on gate failure. Run: `python3 eval_b.py --days 14`.
- **Gap 8 — Silent pool scoring failure** (`orchestrator.py`): `score_today()` and `write_daily_performance()` wrapped in `try/except` with warning prints. EOD no longer crashes silently if pool scoring fails.
- `from __future__ import annotations` added to `orchestrator.py` for Python 3.9 compatibility with `dict | None` type hints.
- 52 new tests: `test_mtm_loss_limit.py` (7), `test_eod_dedup_and_logging.py` (10), `test_eval_b.py` (23), `test_gap_fixes.py` (updated, +3 mocks fixed).
- Tests: 237 passing.

### v1.7 — 2026-05-23

**Friction gap reconciliation fix**

- `agents/pool_scorer.py`: Added `_alpaca_order_pnl()` helper. Fetches today's `stratb_`-tagged BUY orders from Alpaca, computes P&L from bracket exit leg fills, falls back to DB `realized_pnl` for manual closes. `friction_gap` is now per-strategy, not combined A+B account equity.
- `alpaca_equity` kept as informational (combined A+B, labelled clearly in the performance row).
- 3 new tests: bracket exit P&L, manual fallback, wrong-tag filter.
- Tests: 185 passing.

### v1.6 — 2026-05-23

**P1: datetime reconcile fix, friction breakdown, ATR quality gate**

- `agents/alpaca_broker.py` `_reconcile_with_alpaca`: wrapped `(o.filled_at or o.submitted_at or "")` with `str()` — `filled_at` is a `datetime` object; the missing `str()` raised `AttributeError`, silently caught, leaving `filled_buys = set()` and causing all positions to be marked UNFILLED.
- `agents/pool_scorer.py` `write_daily_performance`: added `friction_breakdown` dict (total_entry_slippage_bps, avg_slippage_bps, fills_with_data) to the `pool=None` total row, computed from fill_price vs entry_price on today's closed positions.
- `scanner/scanner.py` `_score_ticker`: computes `atr_pct` from absolute ATR / price and exposes it in candidate output (needed by ATR sizer). Adds ATR quality gate: `MAX_ATR_PCT=3.0` — skips stocks with ATR% >3 before they reach Claude (stricter than Strategy A because blue chip universe should not have ATR >3%).
- `config/settings.py`: `MAX_ATR_PCT = 3.0` added.
- Tests: 182 passing (+1 new: datetime reconcile fix regression test).

### v1.5 — 2026-05-23

**P0: ATR-based stop sizing and ORB choppiness gate**

Root cause addressed: fixed 0.67% stop was inside the intraday noise band for most stocks (IONQ ATR 8.39% vs 0.67% stop = 0.08× ratio). Of 27 stop exits analysed, 3 with ATR data confirmed 100% noise stops.

- `agents/atr_sizer.py` (new): `apply()` runs between sector guard and guardrails. For each trade, looks up `atr_pct` from scanner candidates. Computes `stop_pct = max(atr_pct × 1.2, 0.5%)`. Shares = min($150/risk, position_cap/entry) — constant dollar risk regardless of stop width. ORB gate: fetches first-30-min opening range via yfinance; if ORB < 0.5 × ATR, the open was directionless → halve shares. Trades where stop_pct ≥ target_pct (R:R < 1) are dropped.
- `agents/guardrails.py`: `_validate()` updated to accept `atr_stop_pct` field. When present, verifies stop matches ATR formula (not fixed 0.67% formula). Fixed formula check unchanged for trades without the field.
- `orchestrator.py`: step 5.7 calls `atr_sizer.apply()` after sector guard (premarket). Intraday path also calls it; momentum scan candidates carry no ATR, so all pass through unchanged.
- `config/settings.py`: `ATR_STOP_MULTIPLIER = 1.2`, `ATR_STOP_FLOOR = 0.005`, `MAX_LOSS_DOLLARS = 150`, `ORB_ATR_FLOOR = 0.5` added.
- Tests: 17 new tests in `tests/test_atr_sizer.py` + 3 new guardrails ATR tests; 181 tests passing.

### v1.4 — 2026-05-23

**Raise ceiling from 2.5% to 4% on Leg B**

- `config/settings.py`: `TARGET_PCT` 0.025 → 0.04. Trail does the actual exit work on most trades; 2.5% was prematurely capping strong momentum days (3–4% straight-line runs).
- `agents/guardrails.py`: Formula validation now accepts either `TARGET_PCT` (4% premarket ceiling) or `INTRADAY_TARGET_PCT` (1% intraday cap). Previously only checked against `TARGET_PCT`, which caused intraday trades to be rejected after the ceiling raise.

### v1.3 — 2026-05-23

**Stock selection improvements — P0/P1/P2 quality sweep**

**Scanner — breakout freshness scoring**
- `scanner/scanner.py`: Added breakout freshness classification to `_score_ticker()`. Price 0–5% above SMA20 → `FRESH` (+1 score, high continuation odds). Price >12% above SMA20 → `EXTENDED` (-1 score, mean-reversion risk). 5–12% or below SMA20 → `NORMAL`. Field `breakout_freshness` now included in all candidate dicts.

**Scanner — RS flat-day guard**
- `scanner/scanner.py`: RS vs sector ETF only fires when sector return is >0.3% (was 0.1%). Flat sector days were producing extreme RS ratios (e.g., stock up 0.4% / sector up 0.05% = RS of 8x) that were meaningless noise. Raised threshold prevents false strong-RS signals on quiet days.

**Pool Filter — vol ratio bug fix**
- `scanner/pool_filter.py`: `_realtime_metrics()` changed `period="5d"` → `period="30d"`. With only 5 days of data, `tail(20)` was averaging only 5 rows — the 20-day average was actually a 5-day average. Now uses a proper 20-day baseline.

**Pool Filter — RS flat-day guard**
- `scanner/pool_filter.py`: Same fix as scanner — both `sector_return` and `spy_return` thresholds raised from 0.05% to 0.3% to suppress noise on flat days.

**Pool Filter — quality floor**
- `scanner/pool_filter.py`: Added `POOL3_MIN_FILTER_SCORE` gate. Before selecting top N, all tickers must pass `filter_score > POOL3_MIN_FILTER_SCORE` (default 0.0). Prevents net-negative-signal stocks from entering Pool 3 just because they rank highest on a weak day.

**Pool Manager — seed stock demotion immunity removed**
- `core/pool_manager.py`: Removed `ticker not in POOL_2_SEED` guard from the Pool 2 demotion condition. Previously seed stocks were immune to demotion — underperformers in the seed list persisted in Pool 2 indefinitely. Now all Pool 2 stocks, including seeds, are demoted when `rolling_score ≤ POOL_DEMOTION_SCORE`.

**Market Context — sector rotation**
- `agents/market_context.py`: Added sector rotation to `get()`. Fetches 2-day returns for 11 sector ETFs (XLK, XLF, XLE, XLV, XLI, XLC, XLY, XLP, XLB, XLRE, XLU), sorts best→worst, and includes leading/lagging sectors in Claude's market summary. Gives Claude directional context about which sectors have intraday momentum.

### v1.2 — 2026-05-22

**Fix: Intraday momentum candidates now include `pool` field**

- `scanner/intraday_momentum.py`: Momentum candidate dict now always includes `pool: 2` field alongside `signal_type: INTRADAY_MOMENTUM`
- `agents/strategy.py` prompt: Explicitly exempts `signal_type: INTRADAY_MOMENTUM` candidates from the 1pm pool restriction. These are confirmed movers valid at any hour, not speculative setups that need time-of-day protection
- **Root cause:** Strategy prompt applied 1pm pool restriction to all non-Pool-3 entries, including confirmed momentum plays. The pool field was absent from intraday_momentum output, making it impossible for Claude to correctly classify them
- **Impact:** INTRADAY_MOMENTUM entries were being filtered out after 1pm even on days with strong SPY momentum and clear Pool 3 movers

### v1.1 — 2026-05-20

- Initial pool system implementation
- Pool Filter scoring: 5 signals (VWAP, ORB, volume ratio, sector RS, volume acceleration)
- Pool Scorer EOD: rolling 7-day scoring, promote/demote logic
- Intraday momentum scanner with SPY gate and max-runs guard
- Partial profit design (Leg A/Leg B) from Strategy A

### v1.0 — 2026-05-01

- Initial deployment
- Static Pool 2 seed from config
- Strategy B premarket pipeline live
- b_ prefixed Supabase tables
