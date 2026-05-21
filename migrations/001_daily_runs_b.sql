-- Migration 001 (Strategy B): b_daily_runs table + run_id on b_positions
-- Run once in the Supabase SQL editor, then run validate_daily_runs_b.py

create table if not exists b_daily_runs (
  id              uuid primary key default gen_random_uuid(),
  date            date        not null,
  run_type        text        not null check (run_type in ('premarket', 'intraday')),
  run_number      integer     not null default 0,
  started_at      timestamptz not null default now(),
  positions_opened integer    not null default 0,
  loss_guard_active boolean   not null default false,
  created_at      timestamptz not null default now(),
  unique (date, run_number)
);

create index if not exists idx_b_daily_runs_date on b_daily_runs(date);

alter table b_positions add column if not exists run_id uuid references b_daily_runs(id);
create index if not exists idx_b_positions_run_id on b_positions(run_id);
