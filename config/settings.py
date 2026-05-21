import os
from dotenv import load_dotenv

load_dotenv()

# API Keys — same credentials as Strategy A
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
SUPABASE_URL       = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY       = os.getenv("SUPABASE_KEY", "")
ALPACA_API_KEY     = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY  = os.getenv("ALPACA_SECRET_KEY", "")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "changeme")

STRATEGY_TAG = "b"   # tag on every Alpaca order and Supabase row

# Capital — same as Strategy A for fair comparison
TOTAL_CAPITAL        = 50_000
DAILY_PROFIT_TARGET  = 500
MAX_POSITION_PCT     = 0.07
MIN_POSITION_PCT     = 0.05
MAX_POSITIONS        = 10           # fewer positions — blue chip focus
MAX_LOSS_PER_TRADE   = 0.0067
MIN_REWARD_RISK      = 2.9
TARGET_PCT           = 0.02
MAX_PER_SECTOR       = 2           # tighter sector limit — 25 stock universe
DAILY_LOSS_LIMIT     = -300
PRICE_SANITY_PCT     = 0.05
DAILY_LOCK_IN_TARGET = 716
DAILY_BONUS_TARGET   = 1_000
LOCK_IN_TRAIL_PCT    = 0.005
TRAIL_PCT            = 0.01
PARTIAL_PROFIT_ENABLED = True
PARTIAL_PROFIT_PCT   = 0.01

POSITION_SIZE_BY_CONFIDENCE = {
    "HIGH":   3_500,
    "MEDIUM": 3_000,
    "LOW":    2_500,
}

# Scanner thresholds — tighter for blue chip quality
RSI_OVERSOLD         = 35
RSI_OVERBOUGHT       = 65
MIN_VOLUME_RATIO     = 0.3         # vs 20-day avg
MIN_PRICE            = 15.0        # blue chips only — higher floor
MIN_AVG_VOLUME       = 5_000_000   # 5M+ avg volume (blue chip floor)
SCORE_THRESHOLD      = 1

# Intraday scan windows
INTRADAY_SCAN_UTC_START         = 15   # 11:00 AM ET
INTRADAY_SCAN_UTC_END           = 20   # buffer past 3:00 PM ET
INTRADAY_SCAN_MAX_RUNS          = 3
INTRADAY_SCAN_MIN_INTERVAL_MINS = 120  # 11 AM, 1 PM, 3 PM ET
INTRADAY_TARGET_PCT             = 0.01
MIN_INTRADAY_MOVE_PCT           = 2.0   # lower bar for blue chips — less volatile

# Pool system thresholds
POOL_PROMOTION_SCORE   = 6.0    # 7-day rolling score to promote Pool 1 → Pool 2
POOL_DEMOTION_SCORE    = 2.0    # 7-day rolling score to demote Pool 2 → Pool 1
POOL3_SIZE             = 10     # max stocks in Pool 3 each day
POOL3_MIN_VOL_RATIO    = 0.3    # min relative volume for Pool 3 selection
POOL3_EARNINGS_DAYS    = 2      # exclude stocks with earnings within N days

# Scoring weights for daily stock scoring
SCORE_WEIGHT_WIN_LOSS  = 0.40
SCORE_WEIGHT_PNL       = 0.30
SCORE_WEIGHT_SLIPPAGE  = 0.20
SCORE_WEIGHT_SETUP     = 0.10
SCORE_ROLLING_DAYS     = 7
SCORE_RECENT_MULTIPLIER = 2.0   # last 2 days count double
