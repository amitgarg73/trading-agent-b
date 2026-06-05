"""
Health check — verifies Supabase and Alpaca connectivity before market open,
and detects missed EOD sessions that would leave positions carrying overnight.

Runs twice daily via GitHub Actions:
  09:00 ET (13:00 UTC) — pre-open: connectivity + stale positions + yesterday's EOD
  16:30 ET (20:30 UTC) — post-EOD: today's EOD ran + no stale positions remain
"""
import sys
from datetime import date, datetime, timedelta, timezone
from core import db
from core.alerts import send_alert
from config.settings import ALPACA_API_KEY, ALPACA_SECRET_KEY


def _is_weekday(d: date) -> bool:
    return d.weekday() < 5  # Mon-Fri


def _last_trading_day(from_date: date) -> date:
    """Return the most recent weekday before from_date."""
    d = from_date - timedelta(days=1)
    while not _is_weekday(d):
        d -= timedelta(days=1)
    return d


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


def check_stale_positions() -> bool:
    try:
        today = date.today().isoformat()
        open_pos = db.select("b_positions", filters={"status": "OPEN"})
        stale = [p for p in open_pos if not (p.get("opened_at") or "").startswith(today)]
        if stale:
            tickers = ", ".join(p["ticker"] for p in stale)
            send_alert(
                f"[Strategy B] Stale open positions - {today}",
                f"Positions carrying overnight: {tickers}\n"
                "EOD force-close may have failed or been missed.\n"
                "Check Alpaca and close manually if needed.",
            )
            print(f"❌ Stale positions: {tickers} — alert sent")
            return False
        print(f"✅ Positions: no stale open positions ({len(open_pos)} open today)")
        return True
    except Exception as e:
        print(f"❌ Stale position check: {e}")
        return False


def check_eod_ran(check_date: str) -> bool:
    """Return True if EOD logged run_eod_started for check_date."""
    try:
        rows = db.select("b_scan_results", filters={
            "date":      check_date,
            "scan_type": "run_eod_started",
        })
        if rows:
            print(f"✅ EOD: ran for {check_date}")
            return True

        send_alert(
            f"[Strategy B] EOD session MISSED - {check_date}",
            f"No EOD run detected for {check_date}.\n"
            "Open positions may be carrying overnight. "
            "Check Alpaca and run EOD manually:\n"
            "  python orchestrator.py --mode eod",
        )
        print(f"❌ EOD MISSED for {check_date} — alert sent")
        return False
    except Exception as e:
        print(f"❌ Missed-EOD check failed: {e}")
        return False


def check_pool_seeded() -> bool:
    try:
        pool2 = db.select("b_pools", filters={"pool": 2})
        if pool2:
            print(f"✅ Pool 2: {len(pool2)} stocks seeded")
            return True
        else:
            print("⚠️  Pool 2: not seeded yet — will seed on first premarket run")
            return True  # not a fatal error
    except Exception as e:
        print(f"❌ Pool check: {e}")
        return False


if __name__ == "__main__":
    # Auto-detect mode from UTC hour:
    #   pre-open  (< 20 UTC = before 4 PM ET): check yesterday's EOD + connectivity
    #   post-EOD  (≥ 20 UTC = after  4 PM ET): check today's EOD + stale positions
    # Threshold is 20:00 UTC so EOD (19:55 UTC) always finishes before post-EOD check.
    utc_hour = datetime.now(timezone.utc).hour
    today    = date.today()
    is_pre_open = utc_hour < 20

    print(f"\n{'='*55}")
    mode_label = "PRE-OPEN" if is_pre_open else "POST-EOD"
    print(f"  Strategy B — Health Check [{mode_label}] — {today}")
    print(f"{'='*55}\n")

    results: list[bool] = []

    if is_pre_open:
        # Morning run: connectivity + stale positions + did yesterday's EOD run?
        results.append(check_supabase())
        results.append(check_alpaca())
        results.append(check_stale_positions())
        results.append(check_pool_seeded())
        yesterday = _last_trading_day(today)
        if _is_weekday(yesterday):
            results.append(check_eod_ran(yesterday.isoformat()))
    else:
        # Evening run: did today's EOD run + no stale positions remain?
        results.append(check_supabase())
        if _is_weekday(today):
            results.append(check_eod_ran(today.isoformat()))
        results.append(check_stale_positions())

    print()
    if all(results):
        print("✅ Strategy B: all checks passed")
        sys.exit(0)
    else:
        print("❌ Strategy B: health check failed")
        sys.exit(1)
