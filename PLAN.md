# Project Plan — Trading Agent B

## Phase 1: Build & Launch (Week 1)

### Day 1–2: Foundation
- [x] Repo scaffolding, docs, schema
- [x] config/blue_chips.py — 25 blue chip pool seed
- [x] config/settings.py — Strategy B parameters
- [x] core/db.py — Supabase client (b_ tables)
- [x] schema_b.sql — all b_ tables

### Day 2–3: Scanner + Pool Filter
- [ ] scanner/scanner.py — behavioral scoring
- [ ] scanner/pool_filter.py — Pool 2→3 daily selection
- [ ] tests/test_scanner.py + test_pool_filter.py

### Day 3–4: Agents
- [ ] agents/strategy.py — blue chip Claude prompt
- [ ] agents/risk.py — adapted risk checks
- [ ] agents/guardrails.py — adapted guardrails
- [ ] agents/alpaca_broker.py — strategy_b tagged
- [ ] agents/pool_scorer.py — EOD daily scoring
- [ ] tests for each agent

### Day 4–5: Orchestrator + GitHub Actions
- [ ] orchestrator.py — premarket/intraday/eod
- [ ] .github/workflows/trading.yml
- [ ] .github/workflows/test.yml
- [ ] .github/workflows/health_check.yml

### Day 5–7: Dashboard + First Run
- [ ] dashboard/app.py — Strategy B view
- [ ] dashboard/app.py — A vs B comparison page
- [ ] Deploy to Streamlit Cloud
- [ ] Set GitHub secrets
- [ ] First paper trading run

---

## Phase 2: Dynamic Scoring (Day 30+)

- Enable pool promotion/demotion based on 30 days of scoring data
- Tune Pool 3 filter thresholds based on early results
- Add sector-level scoring (which sectors produce best setups)
- Enhanced dashboard: pool performance heatmap

---

## Phase 3: Capital Allocation (Post June 8 eval)

- June 8: Run Strategy A eval (`python3 eval.py --days 14`)
- Compare Strategy A vs B across same period
- Allocate paper capital weighting toward better performer
- Decide on real money deployment readiness

---

## Key Milestones

| Milestone | Target Date |
|-----------|-------------|
| First paper trade | 2026-05-23 |
| 7-day scoring data | 2026-05-30 |
| June 8 eval (both strategies) | 2026-06-08 |
| Pool promotion live | 2026-06-15 |
| 30-day full comparison | 2026-06-20 |
