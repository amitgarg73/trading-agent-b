"""
Manual override control for Strategy B.
Clears the halt flag so premarket/intraday/eod can resume.

Usage:
    python control_b.py --action restart
    python control_b.py --action status
"""
import argparse
from core import db


def restart() -> None:
    active = db.select("b_scan_results", filters={"scan_type": "halt_flag"})
    if not active:
        print("✅  No halt flag found — Strategy B is already running.")
        return
    for row in active:
        db.update("b_scan_results", {"id": row["id"]}, {"scan_type": "halt_flag_cleared"})
    print(f"✅  Halt flag cleared ({len(active)} record(s)). Strategy B will resume on the next scheduled run.")
    print("   To trigger immediately: use the 'trading.yml' workflow_dispatch with mode=premarket.")


def status() -> None:
    rows = db.select("b_scan_results", filters={"scan_type": "halt_flag"})
    if rows:
        r = rows[0].get("results", {})
        print(f"🛑  HALTED — {r.get('reason', 'unknown reason')}")
        print(f"    Since: {r.get('halted_at', 'unknown')}")
    else:
        print("✅  RUNNING — no halt flag set.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategy B manual override")
    parser.add_argument("--action", choices=["restart", "status"], required=True)
    args = parser.parse_args()
    if args.action == "restart":
        restart()
    elif args.action == "status":
        status()
