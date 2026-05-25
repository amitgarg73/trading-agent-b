"""
Local resilience ledger.

Writes key trading events to a date-stamped JSONL file as a fallback
if Supabase is unavailable. Keeps a rolling 3-day window of files.

Events written here:
  run_started / run_completed / run_failed  — orchestrator lifecycle
  trade_opened     — position confirmed in DB + Alpaca
  trade_cancelled  — order rejected, price drift, or no fill confirmation
  trade_closed     — bracket exit or EOD close reconciled
  trade_unfilled   — entry never executed
  pnl_recorded     — EOD daily_performance written to DB

Never raises — a ledger failure must not stop trading.
"""
from __future__ import annotations
import json
from datetime import date, datetime, timedelta
from pathlib import Path

_DATA_DIR  = Path(__file__).parent.parent / "data"
_KEEP_DAYS = 3


def _today_path() -> Path:
    return _DATA_DIR / f"ledger_{date.today().isoformat()}.jsonl"


def _cleanup() -> None:
    """Delete ledger files older than _KEEP_DAYS days."""
    try:
        cutoff = date.today() - timedelta(days=_KEEP_DAYS)
        for f in _DATA_DIR.glob("ledger_*.jsonl"):
            try:
                file_date = date.fromisoformat(f.stem.replace("ledger_", ""))
                if file_date < cutoff:
                    f.unlink()
            except (ValueError, OSError):
                pass
    except Exception:
        pass


def log(event: str, data: dict | None = None) -> None:
    """Append one event line to today's ledger file."""
    try:
        _DATA_DIR.mkdir(exist_ok=True)
        _cleanup()
        record = {
            "ts":    datetime.utcnow().isoformat(),
            "event": event,
            "data":  data or {},
        }
        with _today_path().open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        print(f"  ⚠️  Ledger write failed ({event}): {e}")


def read_today() -> list[dict]:
    """Return all events logged today. Empty list if file missing."""
    try:
        path = _today_path()
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except Exception:
        return []
