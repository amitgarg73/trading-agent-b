-- Strategy B schema — run in Supabase SQL editor
-- All tables prefixed b_ to coexist with Strategy A tables

-- Pool membership — which stocks are in which pool
create table if not exists b_pools (
    id          uuid primary key default gen_random_uuid(),
    ticker      text not null,
    pool        int  not null check (pool in (1, 2, 3)),
    added_at    date not null default current_date,
    promoted_from int,                    -- which pool it came from
    rolling_score numeric default 0,      -- last computed 7-day score
    trade_count   int default 0,          -- total trades taken
    win_count     int default 0,          -- total wins
    updated_at  timestamptz default now(),
    unique(ticker)
);

-- Daily stock scores — written each EOD
create table if not exists b_stock_scores (
    id              uuid primary key default gen_random_uuid(),
    date            date not null,
    ticker          text not null,
    pool            int,
    traded          boolean default false,
    win             boolean,              -- null if not traded
    pnl             numeric,             -- null if not traded
    slippage_bps    numeric,             -- null if not traded
    setup_score     numeric,             -- scanner signal quality 0-10
    daily_score     numeric,             -- composite daily score
    rolling_7d      numeric,             -- 7-day weighted rolling score
    created_at      timestamptz default now(),
    unique(date, ticker)
);

-- Daily trade plans
create table if not exists b_trade_plans (
    id                      uuid primary key default gen_random_uuid(),
    date                    date not null unique,
    market_context          text,
    pool3_tickers           text[],       -- which tickers were in Pool 3 today
    total_estimated_profit  numeric,
    risk_note               text,
    status                  text default 'ACTIVE',
    created_at              timestamptz default now()
);

-- Individual planned trades
create table if not exists b_planned_trades (
    id              uuid primary key default gen_random_uuid(),
    plan_id         uuid references b_trade_plans(id),
    ticker          text not null,
    pool            int not null,         -- which pool this trade came from
    action          text not null,        -- BUY
    entry_price     numeric,
    target_price    numeric,
    stop_loss       numeric,
    position_size   numeric,
    shares          integer,
    estimated_profit numeric,
    confidence      text,                 -- HIGH, MEDIUM, LOW
    reasoning       text,
    status          text default 'PLANNED',
    created_at      timestamptz default now()
);

-- Open and closed positions
create table if not exists b_positions (
    id                  uuid primary key default gen_random_uuid(),
    planned_trade_id    uuid references b_planned_trades(id),
    ticker              text not null,
    pool                int not null,
    action              text not null,
    entry_price         numeric not null,
    current_price       numeric,
    target_price        numeric,
    stop_loss           numeric,
    shares              integer,
    position_size       numeric,
    unrealized_pnl      numeric default 0,
    status              text default 'OPEN',
    opened_at           timestamptz default now(),
    closed_at           timestamptz,
    close_price         numeric,
    realized_pnl        numeric,
    close_reason        text,             -- TARGET, STOP, EOD, MANUAL
    alpaca_order_id     text,
    high_watermark      numeric,
    exit_mechanism      text              -- TARGET, MANUAL_TRAIL, STOP, EOD
);

-- Daily P&L summary per pool
create table if not exists b_daily_performance (
    id              uuid primary key default gen_random_uuid(),
    date            date not null,
    pool            int,                  -- null = total across all pools
    trades_taken    int default 0,
    wins            int default 0,
    losses          int default 0,
    gross_pnl       numeric default 0,
    win_rate        numeric,
    avg_pnl_per_trade numeric,
    expectancy      numeric,
    -- Regime logging — passive observation, no hard gate
    vix_level       numeric,
    fear_greed      int,
    spy_change_pct  numeric,
    regime_label    text,                 -- TREND | CHOPPY | HIGH_VOL | FEAR
    created_at      timestamptz default now(),
    unique(date, pool)
);

-- Add regime columns to existing table if already created
alter table b_daily_performance add column if not exists vix_level numeric;
alter table b_daily_performance add column if not exists fear_greed int;
alter table b_daily_performance add column if not exists spy_change_pct numeric;
alter table b_daily_performance add column if not exists regime_label text;

-- Equity reconciliation columns
alter table b_daily_performance add column if not exists alpaca_equity numeric;
alter table b_daily_performance add column if not exists friction_gap  numeric;
alter table b_daily_performance add column if not exists friction_breakdown jsonb;

-- P0: Execution quality columns
-- fill_price: actual Alpaca fill price (vs entry_price which is the planned price)
-- low_watermark: lowest price reached while open (for MAE calculation)
-- mae: Maximum Adverse Excursion in dollars (how far against us it went)
-- mfe: Maximum Favorable Excursion in dollars (how far in our favour it went)
alter table b_positions add column if not exists fill_price numeric;
alter table b_positions add column if not exists low_watermark numeric;
alter table b_positions add column if not exists mae numeric;
alter table b_positions add column if not exists mfe numeric;
alter table b_positions add column if not exists trail_order_id text;

-- Intraday scan tracking (used by _maybe_run_intraday_scan in orchestrator)
create table if not exists b_scan_results (
    id          serial primary key,
    date        text not null,
    scan_type   text not null default 'intraday_scan',
    scanned_at  text not null,
    candidates  int  not null default 0,
    placed      int  not null default 0,
    results     jsonb
);
create index if not exists idx_b_scan_results_date on b_scan_results(date);

-- Indexes for common queries
create index if not exists idx_b_positions_status  on b_positions(status);
create index if not exists idx_b_positions_ticker  on b_positions(ticker);
create index if not exists idx_b_stock_scores_date on b_stock_scores(date);
create index if not exists idx_b_stock_scores_ticker on b_stock_scores(ticker);
create index if not exists idx_b_pools_pool        on b_pools(pool);
