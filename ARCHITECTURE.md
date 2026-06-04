# Strategy B — Architecture

Blue chip pool-based pipeline. A three-tier pool system curates stocks behaviorally over time so Claude sees only battle-tested candidates with a performance track record. Two daily entry paths: premarket scan at 10 AM and two intraday momentum scans at 10:30 AM / 11:30 AM.

---

## Daily Schedule

```
10:00 AM ET   trading.yml → premarket   Pool 3 → scan → Claude → orders
10:00–3:59 PM trading.yml → intraday    Every 15 min: sync, trail/stop/target exits
10:30 AM ET   entry_scan.yml            Intraday momentum scan #1
11:30 AM ET   entry_scan.yml            Intraday momentum scan #2
 3:55 PM ET   trading.yml → eod         Force-close, score, pool promotions, write performance
```

---

## Pool System — The Core Differentiator

```mermaid
flowchart TD
    P1["Pool 1 (~80 stocks)\nS&P 500 liquid names\nAvg daily volume > 5M\nCandidates for promotion"]:::pool1

    P2["Pool 2 (40 stocks)\nCurated blue chips\nMag 7 + FAANG\nFinancials, Healthcare\nEnergy, Consumer, AI\nSeed stocks never demoted"]:::pool2

    P3["Pool 3 (top 20 daily)\nEach morning:\npool_filter selects top 20\nby composite filter_score\nvol_ratio + VWAP + ORB\n+ volume acceleration\n+ sector RS + market RS"]:::pool3

    SCANNER["daily_scanner\n+ ATR signals"]:::agent
    CLAUDE["Claude\nclaude-sonnet-4-6\nFinal trade selection\nfrom Pool 3 context"]:::llm

    P1 -->|rolling_7d ≥ 6.0| P2
    P2 -->|rolling_7d ≤ 2.0| P1
    P2 --> P3
    P3 --> SCANNER
    SCANNER --> CLAUDE

    SCORER["EOD: pool_scorer\nper-trade: win/loss\nP&L · slippage · setup\nrolling 7-day average\n2x weight on last 2 days"]:::agent
    CLAUDE --> SCORER
    SCORER -->|update rolling_7d| P1
    SCORER -->|update rolling_7d| P2

    classDef pool1 fill:#dbeafe,stroke:#3b82f6,color:#1e3a5f
    classDef pool2 fill:#fef3c7,stroke:#f59e0b,color:#78350f
    classDef pool3 fill:#dcfce7,stroke:#22c55e,color:#14532d
    classDef agent fill:#f3e8ff,stroke:#a855f7,color:#4a044e
    classDef llm fill:#fee2e2,stroke:#ef4444,color:#7f1d1d
```

---

## Full Premarket Pipeline

```mermaid
flowchart TD
    GHA["GitHub Actions\n10:00 AM ET"]:::infra --> PM["orchestrator.premarket()"]

    PM --> CHECKS["Pre-checks\n─────────────────\n_is_trading_day() — Alpaca calendar\n_is_halted() — b_scan_results\nDuplicate guard — b_trade_plans\n_sweep_and_verify() — close overnight"]:::filter

    CHECKS --> POOL3["① pool_filter.get_pool3_with_context()\n─────────────────\nReads b_pools (Pool 2)\n3 Alpaca batch calls:\n  snapshots · 30d daily bars · 5-min bars\nScores by: vol_ratio + VWAP reclaim\n  + ORB breakout + vol_acceleration\n  + sector RS + market RS + return\nReturns top 20 by filter_score"]:::agent

    POOL3 --> MC["② market_context.get()\n─────────────────\nyfinance: ^VIX\nAlpaca: SPY daily bars (futures_bias)\nCNN API: Fear & Greed 0–100\nAlpaca: 11 sector ETF daily bars"]:::agent

    MC --> SCAN["③ scanner.run_scan(pool3_tickers)\n─────────────────\nyfinance: 3-month daily bars\n8 parallel workers\nRSI(14) · MACD · Bollinger\nSMA20/50 · ATR(14)\nbehavioral signals"]:::agent

    SCAN --> MERGE["Pool 3 fill-in\npool_filter data fills gaps\nwhere yfinance scanner missed\npool3 tickers"]:::filter

    MERGE --> GAP["③.1 Gap-up injection\n─────────────────\nAlpaca Screener ≥2% movers\nFilter to Pool 3 only\nTop 20, not already scanned"]:::agent

    GAP --> GFILTER["③.15 Garbage filter\nprice > 0 AND rsi ≠ None"]:::filter

    GFILTER --> ENRICH["③.2 Live price + signals (parallel)\n─────────────────\nAlpaca StockLatestQuoteRequest → ask\nAlpaca StockSnapshotRequest:\n  above_vwap · vwap · today_pct\n  rs_vs_spy · day_high · day_low\nExtension filter: >3% + vol<0.7x\nORB: logged, not hard-gated\nTop-of-range: >85% day range"]:::agent

    ENRICH --> NEWS["③.5 news_intel.run(candidates)\n─────────────────\nyfinance .news per ticker\n20 workers · 30s timeout\nEarnings blackout DISABLED\nPasses news_context to Claude"]:::agent

    NEWS --> CAP["③.8 Trade cap\ntop N by technical_score\nMax: min(MAX_DAILY_ENTRIES, max_positions)"]:::filter

    CAP --> CLAUDE["④ strategy.select_trades()\n─────────────────\nModel: claude-sonnet-4-6\nPrompt caching (ephemeral)\nClaude sees per-stock:\n  pool · rolling_score · above_vwap\n  rs_vs_sector · atr_ratio\n  behavioral_signals · technical_score\nTime-of-day rules in system prompt"]:::llm

    CLAUDE --> RISK["⑤ risk.validate(trades)\n─────────────────\nMTM loss limit check\nMax positions (10)\nSector cap (2 per sector)\nDuplicate guard\nR:R ≥ 1.4"]:::agent

    RISK --> SG["⑤.5 sector_guard.run()\n─────────────────\nSECTOR_MAP from blue_chips.py\nMax 2 per sector\nDrops lowest confidence excess"]:::agent

    SG --> ATR["⑤.7 atr_sizer.apply()\n─────────────────\nStop = max(ATR × 1.2, 0.5%)\n$150 constant dollar risk\nORB < 0.5×ATR → halve shares\nDrops if R:R < MIN_REWARD_RISK"]:::agent

    ATR --> GUARD["⑥ guardrails.check()\n─────────────────\nRequired fields\nBUY-only\nFormula validation (8% target)\nLive price sanity ±5% (Alpaca)\nBuying power (Alpaca account)"]:::agent

    GUARD --> SAVE["⑦ Save plan\nb_trade_plans + b_planned_trades\nb_daily_runs (premarket)"]:::db

    SAVE --> ORDERS["⑧ place_orders()\n─────────────────\nAlpaca bracket order:\n  entry: limit (hybrid bid/mid)\n  target: limit take-profit (8%)\n  stop: stop-loss leg\nTrail: cancel bracket stop,\n  submit native TrailingStopRequest\nOrder prefix: stratb_{ticker}_{ts}"]:::infra

    ORDERS --> DBPOS[("b_positions\n(status=OPEN)\nfill_price · watermarks\nalpaca_order_id · trail_order_id")]:::db

    classDef agent fill:#dbeafe,stroke:#3b82f6,color:#1e3a5f
    classDef llm fill:#fef3c7,stroke:#f59e0b,color:#78350f
    classDef filter fill:#f3e8ff,stroke:#a855f7,color:#4a044e
    classDef infra fill:#dcfce7,stroke:#22c55e,color:#14532d
    classDef db fill:#fee2e2,stroke:#ef4444,color:#7f1d1d
```

---

## Intraday Position Management (Every 15 min)

```mermaid
flowchart TD
    GHA["GitHub Actions\nevery 15 min"]:::infra --> ID["orchestrator.intraday()"]

    ID --> RECONCILE["_reconcile_with_alpaca()\n─────────────────\nDiff b_positions(OPEN) vs Alpaca\nNative trail exit → CLOSED\nBracket exit → CLOSED\nNo buy evidence → UNFILLED"]:::agent

    RECONCILE --> BACKFILL["Backfill passes\n─────────────────\nfill_price for slow fills\ntrail_order_id for filled entries\n  missing trail assignment"]:::agent

    BACKFILL --> RLADDER["R-Ladder (per position)\n─────────────────\nR = entry_cost − stop_loss\n+1R profit → stop moves to breakeven\n+2R profit → stop moves to entry+R\ndb.update b_positions stop_loss"]:::agent

    RLADDER --> EXITS{Exit\nconditions?}

    EXITS -->|"daily P&L ≥ BONUS_TARGET\n$1,000"| CB["Close ALL\nBONUS_TARGET"]:::agent
    EXITS -->|"price ≥ target_price"| CT["Close: TARGET"]:::agent
    EXITS -->|"price < VWAP\nwhile stop < entry"| CV["Close: VWAP_BREAK"]:::agent
    EXITS -->|"price ≤ watermark × (1−1%)\n(manual fallback)"| CM["Close: MANUAL_TRAIL"]:::agent
    EXITS -->|"price ≤ stop_loss"| CS["Close: STOP"]:::agent
    EXITS -->|"native trail fired\n(Alpaca server-side)"| CN["Close: NATIVE_TRAIL\n(detected via Alpaca order fill)"]:::agent

    CB & CT & CV & CM & CS & CN --> CLOSE["_close_position()\n─────────────────\nCancel bracket + trail orders\nSubmit MarketOrderRequest SELL\nPoll 15s for confirmation\nUpdate: mae · mfe · realized_pnl\nWrite: b_positions CLOSED"]:::agent

    classDef agent fill:#dbeafe,stroke:#3b82f6,color:#1e3a5f
    classDef infra fill:#dcfce7,stroke:#22c55e,color:#14532d
```

---

## Entry Scan (10:30 AM & 11:30 AM ET)

```mermaid
flowchart TD
    GHA["entry_scan.yml\n10:30 AM / 11:30 AM ET"]:::infra --> ES["_maybe_run_intraday_scan()"]

    ES --> GATES{"All gates\nmust pass"}

    GATES -->|"UTC hour 14–20\nmax 2 scans/day\nmin 55 min gap\nopen slots < 10\ndaily entries < 10\nnet P&L > -$500\ntime < noon ET"| SCAN

    GATES -->|any gate fails| SKIP["Skip — log reason"]:::filter

    SCAN["Fresh Pool 3 tickers\nfrom pool_filter"]:::agent --> MOM

    MOM["intraday_momentum.scan()\n─────────────────\nAlpaca snapshot: up ≥ 0.5% from open\nabove VWAP · price confirmation\nSPY/sector gate check"]:::agent --> DEDUP

    DEDUP["Dedup: exclude tickers\nalready traded today\n(open or closed)"]:::filter --> CLAUDE

    CLAUDE["strategy.select_trades()\n─────────────────\nclaude-sonnet-4-6\nNote: 'INTRADAY SCAN –\n  focus on momentum\n  already moving today'\nINTRADAY_MOMENTUM signal\nbypasses 1 PM restriction"]:::llm --> RISK

    RISK["risk.validate()\natr_sizer.apply()\nguardrails.check()"]:::agent --> ORDERS["place_orders()\n→ b_positions\n→ b_planned_trades\n→ b_scan_results\n  (scan_type=intraday_scan)"]:::infra

    classDef agent fill:#dbeafe,stroke:#3b82f6,color:#1e3a5f
    classDef llm fill:#fef3c7,stroke:#f59e0b,color:#78350f
    classDef filter fill:#f3e8ff,stroke:#a855f7,color:#4a044e
    classDef infra fill:#dcfce7,stroke:#22c55e,color:#14532d
```

---

## EOD Session

```mermaid
flowchart TD
    GHA["3:55 PM ET"]:::infra --> EOD["orchestrator.eod()"]
    EOD --> CLOSE["close_all_positions()\n─────────────────\nMarket sell all open b_positions\nOrphan sweep: close stratb_*\npositions in Alpaca not in DB"]:::agent
    CLOSE --> SCORE["pool_scorer.score_today()\n─────────────────\nFor each closed position:\n  win/loss (40%)\n  P&L magnitude (30%)\n  slippage bps (20%)\n  setup quality (10%)\nRolling 7-day weighted avg\n(last 2 days = 2x weight)"]:::agent
    SCORE --> PROMO["pool_manager.apply_promotions()\n─────────────────\nPool 1→Pool 2: rolling_7d ≥ 6.0\nPool 2→Pool 1: rolling_7d ≤ 2.0\nSeed stocks: never demoted\nUpdates b_pools"]:::agent
    PROMO --> PERF["write_daily_performance()\n─────────────────\nP&L by pool (None=total)\nAlpaca order reconciliation:\n  _alpaca_order_pnl() by stratb_ tag\nFriction: avg slippage bps per fill\nRegime: FEAR/HIGH_VOL/TREND/CHOPPY\nWrites b_daily_performance"]:::agent
    PERF --> DB[("b_stock_scores\nb_pools (updated)\nb_daily_performance")]:::db

    classDef agent fill:#dbeafe,stroke:#3b82f6,color:#1e3a5f
    classDef infra fill:#dcfce7,stroke:#22c55e,color:#14532d
    classDef db fill:#fee2e2,stroke:#ef4444,color:#7f1d1d
```

---

## Agent Handshakes — Premarket Sequence

```mermaid
sequenceDiagram
    participant GHA as GitHub Actions
    participant ORC as Orchestrator
    participant PF as pool_filter
    participant MC as market_context
    participant SCAN as scanner
    participant NI as news_intel
    participant STR as strategy (Claude)
    participant RSK as risk
    participant SG as sector_guard
    participant ATR as atr_sizer
    participant GRD as guardrails
    participant ALP as Alpaca API
    participant DB as Supabase DB

    GHA->>ORC: trigger premarket
    ORC->>ALP: _sweep_and_verify() — close overnight positions

    ORC->>PF: get_pool3_with_context()
    PF->>DB: read b_pools (Pool 2 members)
    PF->>ALP: batch snapshots + 30d bars + 5-min bars
    PF-->>ORC: pool3_context[] {ticker, filter_score, vwap, rs_vs_sector...}

    ORC->>MC: get()
    MC->>MC: yfinance ^VIX
    MC->>ALP: SPY daily bars + 11 sector ETF bars
    MC->>MC: CNN Fear & Greed API
    MC-->>ORC: {vix_level, futures_bias, fear_greed, sector_rotation}

    ORC->>SCAN: run_scan(pool3_tickers)
    SCAN->>SCAN: yfinance 3-month bars × 8 workers
    SCAN-->>ORC: candidates[] {score, rsi, atr, signals...}

    Note over ORC: Merge pool3_context + scanner results
    Note over ORC: Gap-up injection (Alpaca Screener ≥2%)

    ORC->>ALP: batch snapshot → live prices + intraday signals
    ALP-->>ORC: {above_vwap, today_pct, rs_vs_spy, day_h/l}

    ORC->>NI: run(candidates)
    NI->>NI: yfinance news per ticker (20 workers)
    NI-->>ORC: {filtered_candidates, news_context}

    ORC->>DB: insert b_scan_results (premarket snapshot)

    ORC->>STR: select_trades(candidates, mkt, pool3_context, news_context)
    Note over STR: claude-sonnet-4-6 · prompt caching<br/>Sees: pool, rolling_score, behavioral_signals
    STR-->>ORC: {trades[], summary}

    ORC->>RSK: validate(trades)
    RSK->>DB: read b_positions (open count, today P&L)
    RSK-->>ORC: (approved[], rejection_reasons[])

    ORC->>SG: run({approved_trades})
    SG-->>ORC: (approved[], sector_blocked[])

    ORC->>ATR: apply(approved, candidates_atr)
    ATR->>ALP: 5-min bars for ORB check
    ATR-->>ORC: (sized_trades[], dropped[])

    ORC->>GRD: check(trades, "alpaca")
    GRD->>ALP: live price sanity + buying power
    GRD-->>ORC: (passed[], rejected[])

    ORC->>DB: insert b_trade_plans + b_planned_trades

    loop Each approved trade
        ORC->>ALP: bracket order (limit + target + stop)
        ALP-->>ORC: (order_id, fill_price)
        ORC->>ALP: cancel bracket stop leg
        ORC->>ALP: submit trailing stop (1%)
        ORC->>DB: insert b_positions (OPEN)
    end
```

---

## Data Model

```mermaid
erDiagram
    b_pools {
        string ticker PK
        int pool "1 or 2"
        float rolling_score "7-day weighted avg 0–10"
        int trade_count
        int win_count
        string promoted_from
        timestamp added_at
    }

    b_positions {
        string id PK
        string ticker
        int pool
        string status "OPEN|CLOSED|UNFILLED"
        float entry_price "planned limit"
        float fill_price "actual Alpaca fill"
        float current_price
        float target_price
        float stop_loss
        int shares
        float position_size
        float unrealized_pnl "from fill_price"
        float realized_pnl
        string close_reason "TARGET|STOP|NATIVE_TRAIL|VWAP_BREAK|MANUAL_TRAIL|BONUS_TARGET|EOD|UNFILLED"
        string exit_mechanism "native_trail|manual|bracket"
        string alpaca_order_id
        string trail_order_id
        string run_id
        float high_watermark
        float low_watermark
        float mae
        float mfe
        timestamp opened_at
        timestamp closed_at
    }

    b_trade_plans {
        string id PK
        date date
        string status "ACTIVE|NO_TRADES|HALTED"
        json pool3_tickers
        string market_context
        float total_estimated_profit
        string risk_note
    }

    b_planned_trades {
        string id PK
        string plan_id FK
        string ticker
        int pool
        float entry_price
        float target_price
        float stop_loss
        float position_size
        int shares
        string confidence
        string reasoning
    }

    b_stock_scores {
        string date PK
        string ticker PK
        int pool
        bool traded
        bool win
        float pnl
        float slippage_bps
        float daily_score "0–10"
        float rolling_7d "weighted 7-day avg"
    }

    b_daily_performance {
        date date PK
        int pool PK "NULL=total 2=Pool2"
        int trades_taken
        int wins
        float gross_pnl
        float win_rate
        float expectancy
        string regime_label "FEAR|HIGH_VOL|TREND|CHOPPY"
        float alpaca_equity
        float friction_gap
    }

    b_pools ||--o{ b_positions : "ticker"
    b_trade_plans ||--o{ b_planned_trades : "plan_id"
    b_positions ||--o{ b_stock_scores : "ticker+date"
```

---

## External Integrations

```mermaid
flowchart LR
    subgraph "Data Sources"
        YF["yfinance\n• 3-month bars per Pool 3 ticker\n• ^VIX\n• Earnings calendar (news_intel)\n• Stock news headlines"]
        CNN["CNN / alternative.me\n• Fear & Greed index 0–100"]
        ALP["Alpaca Markets API\n• Pool 3 scoring: snapshots\n  + 30d bars + 5-min bars\n• SPY + 11 sector ETF bars\n• Live quotes + intraday signals\n• Bracket orders + trailing stops\n• Account equity reconciliation\n• Market movers screener (gap-ups)"]
    end

    subgraph "AI"
        ANT["Anthropic API\n• claude-sonnet-4-6\n• Prompt caching\n• Trade selection\n  (strategy.py only)"]
    end

    subgraph "Storage"
        SB["Supabase\n• b_positions\n• b_pools\n• b_stock_scores\n• b_trade_plans\n• b_planned_trades\n• b_daily_runs\n• b_daily_performance\n• b_scan_results"]
    end

    subgraph "Alerting"
        GM["Gmail\nEOD summary\nError alerts"]
        NTFY["ntfy.sh\nPush notifications"]
    end

    YF & CNN & ALP --> ORC["Orchestrator"]
    ANT --> ORC
    ORC --> SB
    ORC --> GM & NTFY
```

---

## Key Configuration

| Setting | Value | Effect |
|---|---|---|
| `TOTAL_CAPITAL` | $50,000 | Account size |
| `DAILY_PROFIT_TARGET` | $500 | Daily goal |
| `MAX_POSITIONS` | 10 | Concurrent open cap |
| `MAX_DAILY_ENTRIES` | 10 | Total new opens per day |
| `POSITION_SIZE_BY_CONFIDENCE` | HIGH=$3.5K / MED=$3K / LOW=$2.5K | Risk-based sizing |
| `TARGET_PCT` | 8% | Profit ceiling |
| `MIN_REWARD_RISK` | 1.4 | Min R:R (risk check), 2.0 after ATR |
| `ATR_STOP_MULTIPLIER` | 1.2 | Stop = ATR × 1.2 |
| `ATR_STOP_FLOOR` | 0.5% | Minimum stop width |
| `MAX_LOSS_DOLLARS` | $150 | Constant dollar risk per trade |
| `DAILY_LOSS_LIMIT` | -$500 | Gate: no new trades |
| `DAILY_BONUS_TARGET` | $1,000 | Close all: lock in the day |
| `TRAIL_PCT` | 1% | Native Alpaca trailing stop |
| `POOL_PROMOTION_SCORE` | 6.0 | Pool 1 → Pool 2 threshold |
| `POOL_DEMOTION_SCORE` | 2.0 | Pool 2 → Pool 1 threshold |
| `POOL3_SIZE` | 20 | Daily elite picks |
| `MAX_PER_SECTOR` | 2 | Sector concentration cap |
| `INTRADAY_SCAN_MAX_RUNS` | 2 | Max entry scans per day |
| `INTRADAY_ENTRY_CUTOFF_UTC` | 16 | Hard cutoff at noon ET |
| `STRATEGY_TAG` | `stratb` | Alpaca order prefix for isolation |

---

## What Makes Strategy B Different from A

| Dimension | Strategy A | Strategy B |
|---|---|---|
| Universe | 430+ broad scan | 40 curated blue chips (Pool 2) |
| Candidate selection | Scanner score only | pool_filter composite score: vol_ratio + VWAP + ORB + acceleration + RS |
| Claude's context | Raw technical data | + pool membership + rolling_score (7-day P&L quality) + behavioral signals |
| Feedback loop | None | EOD scoring → pool promotions/demotions → next day's candidates |
| Intraday entries | entry_scan re-runs premarket pipeline | Dedicated momentum scan via intraday_momentum.scan() |
| Stop ratchet | None | R-ladder: breakeven at +1R, entry+R at +2R |
| Sector limit | None | Max 2 positions per sector (b_pools sector mapping) |
| EOD reconciliation | P&L from our fills | + Alpaca order reconciliation by stratb_ tag + friction breakdown |
| Shared account | Positions tagged stra_ | Positions tagged stratb_, orphan sweep on EOD |
