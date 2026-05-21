"""
Validate that migrations/001_daily_runs_b.sql has been applied correctly.
Run AFTER executing the SQL in the Supabase dashboard.

Usage: python3 validate_daily_runs_b.py
"""
from dotenv import load_dotenv
load_dotenv()

from core import db

CHECKS = []
ERRORS = []

def check(name: str, ok: bool, detail: str = "") -> None:
    status = "✅" if ok else "❌"
    CHECKS.append((name, ok, detail))
    print(f"  {status}  {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        ERRORS.append(name)

print("\n[ validate_daily_runs_b.py ] Strategy B schema validation\n")

try:
    rows = db.select("b_daily_runs", limit=1)
    check("b_daily_runs table exists", True, f"{len(rows)} existing rows")
except Exception as e:
    check("b_daily_runs table exists", False, str(e))

try:
    db.insert("b_daily_runs", {
        "date": "1970-01-01", "run_type": "premarket", "run_number": 99,
        "started_at": "1970-01-01T00:00:00Z", "positions_opened": 0, "loss_guard_active": False,
    })
    probe = db.select("b_daily_runs", filters={"date": "1970-01-01"})
    db.delete("b_daily_runs", {"date": "1970-01-01"})
    check("b_daily_runs columns correct", bool(probe))
except Exception as e:
    check("b_daily_runs columns correct", False, str(e))

try:
    db.insert("b_daily_runs", {"date": "1970-01-02", "run_type": "premarket",
                                "run_number": 0, "started_at": "1970-01-02T00:00:00Z"})
    dup_blocked = False
    try:
        db.insert("b_daily_runs", {"date": "1970-01-02", "run_type": "premarket",
                                    "run_number": 0, "started_at": "1970-01-02T00:00:00Z"})
    except Exception:
        dup_blocked = True
    db.delete("b_daily_runs", {"date": "1970-01-02"})
    check("UNIQUE(date, run_number) enforced", dup_blocked)
except Exception as e:
    check("UNIQUE(date, run_number) enforced", False, str(e))

try:
    positions = db.select("b_positions", limit=1)
    if positions:
        has_col = "run_id" in positions[0]
        check("b_positions.run_id column exists", has_col)
    else:
        db.select("b_positions", filters={"run_id": None}, limit=1)
        check("b_positions.run_id column exists", True, "no rows but filter accepted")
except Exception as e:
    check("b_positions.run_id column exists", False, str(e))

try:
    all_pos = db.select("b_positions", limit=200)
    clean = all(p.get("run_id") is None for p in all_pos)
    check("existing b_positions unaffected (run_id=NULL)", clean,
          f"{len(all_pos)} legacy rows with NULL run_id")
except Exception as e:
    check("existing b_positions unaffected", False, str(e))

print(f"\n{'─'*50}")
if ERRORS:
    print(f"  ❌  {len(ERRORS)} check(s) failed: {', '.join(ERRORS)}")
    print("\n  Run migrations/001_daily_runs_b.sql in the Supabase SQL editor first.\n")
    raise SystemExit(1)
else:
    print(f"  ✅  All {len(CHECKS)} checks passed — migration is live and correct.\n")
