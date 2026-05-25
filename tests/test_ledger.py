"""
Tests for core/ledger.py.

Covers: file creation, JSONL format, append semantics, rolling 3-day cleanup,
read_today(), and the guarantee that log() never raises on bad input.
"""
import json
import pytest
from datetime import date, timedelta
from unittest.mock import patch
from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _use_tmp(monkeypatch, tmp_path):
    """Point the ledger module at a temp directory for isolation."""
    import core.ledger as ledger
    monkeypatch.setattr(ledger, "_DATA_DIR", tmp_path)
    return ledger


def _make_old_file(tmp_path, days_ago: int) -> Path:
    """Create a dummy ledger file dated `days_ago` days in the past."""
    old_date = (date.today() - timedelta(days=days_ago)).isoformat()
    f = tmp_path / f"ledger_{old_date}.jsonl"
    f.write_text('{"ts":"x","event":"old","data":{}}\n')
    return f


# ── File creation & format ────────────────────────────────────────────────────

class TestLogCreatesFile:
    def test_creates_data_dir_if_missing(self, monkeypatch, tmp_path):
        subdir = tmp_path / "nested"
        ledger = _use_tmp(monkeypatch, subdir)
        ledger.log("run_started", {"mode": "premarket"})
        assert subdir.exists()

    def test_creates_todays_file(self, monkeypatch, tmp_path):
        ledger = _use_tmp(monkeypatch, tmp_path)
        ledger.log("run_started", {"mode": "premarket"})
        expected = tmp_path / f"ledger_{date.today().isoformat()}.jsonl"
        assert expected.exists()

    def test_each_line_is_valid_json(self, monkeypatch, tmp_path):
        ledger = _use_tmp(monkeypatch, tmp_path)
        ledger.log("trade_opened", {"ticker": "AAPL", "shares": 10})
        lines = (tmp_path / f"ledger_{date.today().isoformat()}.jsonl").read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event"] == "trade_opened"
        assert record["data"]["ticker"] == "AAPL"
        assert "ts" in record

    def test_event_with_no_data_writes_empty_dict(self, monkeypatch, tmp_path):
        ledger = _use_tmp(monkeypatch, tmp_path)
        ledger.log("run_completed")
        lines = (tmp_path / f"ledger_{date.today().isoformat()}.jsonl").read_text().splitlines()
        record = json.loads(lines[0])
        assert record["data"] == {}


# ── Append semantics ──────────────────────────────────────────────────────────

class TestAppend:
    def test_multiple_calls_append_not_overwrite(self, monkeypatch, tmp_path):
        ledger = _use_tmp(monkeypatch, tmp_path)
        ledger.log("trade_opened",    {"ticker": "AAPL"})
        ledger.log("trade_cancelled", {"ticker": "TSLA"})
        ledger.log("pnl_recorded",    {"total_pnl": 750.0})
        path = tmp_path / f"ledger_{date.today().isoformat()}.jsonl"
        lines = path.read_text().splitlines()
        assert len(lines) == 3
        events = [json.loads(l)["event"] for l in lines]
        assert events == ["trade_opened", "trade_cancelled", "pnl_recorded"]

    def test_all_event_types_written(self, monkeypatch, tmp_path):
        ledger = _use_tmp(monkeypatch, tmp_path)
        for ev in ["run_started", "run_completed", "run_failed",
                   "trade_opened", "trade_cancelled", "trade_closed",
                   "trade_unfilled", "pnl_recorded"]:
            ledger.log(ev, {"ev": ev})
        path = tmp_path / f"ledger_{date.today().isoformat()}.jsonl"
        lines = path.read_text().splitlines()
        assert len(lines) == 8


# ── Rolling cleanup ───────────────────────────────────────────────────────────

class TestRollingCleanup:
    def test_files_older_than_3_days_are_deleted(self, monkeypatch, tmp_path):
        ledger = _use_tmp(monkeypatch, tmp_path)
        old = _make_old_file(tmp_path, days_ago=4)
        ledger.log("run_started")
        assert not old.exists()

    def test_files_exactly_3_days_old_are_kept(self, monkeypatch, tmp_path):
        ledger = _use_tmp(monkeypatch, tmp_path)
        kept = _make_old_file(tmp_path, days_ago=3)
        ledger.log("run_started")
        assert kept.exists()

    def test_files_within_3_days_are_kept(self, monkeypatch, tmp_path):
        ledger = _use_tmp(monkeypatch, tmp_path)
        d1 = _make_old_file(tmp_path, days_ago=1)
        d2 = _make_old_file(tmp_path, days_ago=2)
        ledger.log("run_started")
        assert d1.exists()
        assert d2.exists()

    def test_only_old_files_removed_not_today(self, monkeypatch, tmp_path):
        ledger = _use_tmp(monkeypatch, tmp_path)
        old  = _make_old_file(tmp_path, days_ago=10)
        kept = _make_old_file(tmp_path, days_ago=3)
        ledger.log("run_started")
        today_file = tmp_path / f"ledger_{date.today().isoformat()}.jsonl"
        assert today_file.exists()
        assert kept.exists()
        assert not old.exists()
        assert len(list(tmp_path.glob("ledger_*.jsonl"))) == 2  # today + 3-day boundary


# ── Resilience ────────────────────────────────────────────────────────────────

class TestNeverRaises:
    def test_non_serializable_data_does_not_raise(self, monkeypatch, tmp_path):
        ledger = _use_tmp(monkeypatch, tmp_path)
        # object() is not JSON serializable — log() must not propagate the error
        ledger.log("trade_opened", {"bad": object()})  # should not raise

    def test_read_only_filesystem_does_not_raise(self, monkeypatch, tmp_path):
        import core.ledger as ledger
        # Point at a path that can never be created
        monkeypatch.setattr(ledger, "_DATA_DIR", Path("/proc/trading_agent_ledger"))
        ledger.log("run_started")  # must not raise


# ── read_today ────────────────────────────────────────────────────────────────

class TestReadToday:
    def test_returns_empty_list_when_no_file(self, monkeypatch, tmp_path):
        ledger = _use_tmp(monkeypatch, tmp_path)
        assert ledger.read_today() == []

    def test_returns_all_todays_events(self, monkeypatch, tmp_path):
        ledger = _use_tmp(monkeypatch, tmp_path)
        ledger.log("trade_opened", {"ticker": "NVDA"})
        ledger.log("trade_closed", {"ticker": "NVDA", "pnl": 120.0})
        events = ledger.read_today()
        assert len(events) == 2
        assert events[0]["event"] == "trade_opened"
        assert events[1]["data"]["pnl"] == 120.0

    def test_returns_empty_list_on_corrupt_file(self, monkeypatch, tmp_path):
        ledger = _use_tmp(monkeypatch, tmp_path)
        path = tmp_path / f"ledger_{date.today().isoformat()}.jsonl"
        path.write_text("not json at all\n{broken")
        assert ledger.read_today() == []
