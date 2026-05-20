"""
Health check — verifies Supabase and Alpaca connectivity before market open.
"""
import sys
from core import db
from config.settings import ALPACA_API_KEY, ALPACA_SECRET_KEY


def check_supabase() -> bool:
    try:
        db.select("b_pools", limit=1)
        print("✅ Supabase: OK")
        return True
    except Exception as e:
        print(f"❌ Supabase: {e}")
        return False


def check_alpaca() -> bool:
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
        account = client.get_account()
        equity = float(account.equity)
        print(f"✅ Alpaca: OK — equity ${equity:,.2f}")
        return True
    except Exception as e:
        print(f"❌ Alpaca: {e}")
        return False


def check_pool_seeded() -> bool:
    try:
        pool2 = db.select("b_pools", filters={"pool": 2})
        if pool2:
            print(f"✅ Pool 2: {len(pool2)} stocks seeded")
            return True
        else:
            print("⚠️ Pool 2: not seeded yet — will seed on first premarket run")
            return True  # not a fatal error
    except Exception as e:
        print(f"❌ Pool check: {e}")
        return False


if __name__ == "__main__":
    results = [check_supabase(), check_alpaca(), check_pool_seeded()]
    if all(results):
        print("\n✅ Strategy B: all systems healthy")
        sys.exit(0)
    else:
        print("\n❌ Strategy B: health check failed")
        sys.exit(1)
