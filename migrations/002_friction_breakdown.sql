-- 002: add friction_breakdown to b_daily_performance
--
-- pool_scorer writes a per-day slippage breakdown (avg/total entry slippage bps,
-- fills_with_data) alongside the headline friction_gap. The column was missing, so the
-- whole EOD performance upsert failed with PGRST204 ("Could not find the
-- 'friction_breakdown' column"). The write path now tolerates the missing column, but
-- apply this so the slippage detail is actually captured.

alter table b_daily_performance add column if not exists friction_breakdown jsonb;
