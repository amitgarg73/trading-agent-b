# Trading Agent B — Blue Chip Pool Strategy

Strategy B is a parallel autonomous trading system running alongside [trading-agent](https://github.com/amitgarg73/trading-agent) (Strategy A). It targets a curated universe of 25 blue chip stocks organized into three dynamic pools, with daily P&L-driven scoring that promotes and demotes stocks between pools.

## Architecture Overview

```
GitHub Actions (scheduler)
        │
        ▼
  orchestrator.py
  ┌─────┴──────────────────────────┐
  │  premarket / intraday / eod    │
  └─────┬──────────────────────────┘
        │
  ┌─────▼──────┐    ┌─────────────┐    ┌──────────────┐
  │  scanner/  │    │   agents/   │    │    core/     │
  │  pool scan │───▶│  strategy   │───▶│  db (b_ tbls)│
  │  pool fil. │    │  risk       │    └──────────────┘
  └────────────┘    │  guardrails │
                    │  alpaca     │
                    │  pool_scorer│
                    └─────────────┘
        │
  ┌─────▼──────────────────────────┐
  │  Supabase (b_ prefixed tables) │
  └─────────────────────────────────┘
        │
  ┌─────▼──────────────────────────┐
  │  Streamlit Dashboard           │
  │  • Strategy B view             │
  │  • Strategy A vs B comparison  │
  └─────────────────────────────────┘
```

## Pool System

| Pool | Size | Description | Update Cadence |
|------|------|-------------|----------------|
| Pool 1 | ~200 | Liquid S&P 500 names (avg vol > 5M) | Monthly |
| Pool 2 | 25–50 | Behavioral shortlist — starts with 25 blue chips | Daily scoring, monthly composition |
| Pool 3 | 8–10 | Daily elite picks filtered from Pool 2 in real time | Every premarket |

## Key Differences from Strategy A

| | Strategy A | Strategy B |
|---|---|---|
| Universe | 430+ tickers | 25–50 curated blue chips |
| Selection | Broad momentum scan | Pool-based behavioral filter |
| Stock scoring | One-time technical score | Daily P&L-driven, rolling 7-day |
| Pool system | None | 3 dynamic pools |
| Focus | Signal breadth | Execution quality |

## Setup

```bash
cp .env.example .env
# Fill in .env with same credentials as Strategy A
pip install -r requirements.txt
# Run schema_b.sql in your Supabase SQL editor
python orchestrator.py --mode premarket --broker alpaca
```

## Running Tests

```bash
pytest tests/ -v
```

## Scheduling

GitHub Actions runs on the same schedule as Strategy A:
- `10:00 AM ET` — premarket scan + pool selection
- `Every 15 min 10:00–3:45 PM ET` — intraday position management
- `4:30 PM ET` — EOD close + daily pool scoring

## Shared Infrastructure

- **Alpaca**: Same paper trading account — trades tagged `strategy=b`
- **Supabase**: Same project — all tables prefixed `b_`
- **Anthropic**: Same API key
- **Combined dashboard**: Strategy B app includes A vs B comparison page
