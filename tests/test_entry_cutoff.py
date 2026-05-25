"""
Tests for RF-5 in Strategy B: INTRADAY_ENTRY_CUTOFF_UTC gate in
orchestrator._maybe_run_intraday_scan().

Late entries (3:00 PM ET = UTC 19+) are negative EV — not enough time
to hit a 1% target. The guard must block new scans at or after hour 19.
"""
import pytest
from unittest.mock import patch
from datetime import datetime, date


def _run_scan_at_hour(utc_hour: int) -> bool:
    """Returns True if scan proceeded to guard checks, False if returned early."""
    from orchestrator import _maybe_run_intraday_scan

    fake_now = datetime(2026, 5, 26, utc_hour, 30, 0)

    with patch("orchestrator.datetime") as mock_dt, \
         patch("orchestrator.date") as mock_date, \
         patch("core.db.select",  return_value=[]) as mock_sel, \
         patch("core.db.insert"):

        mock_dt.utcnow.return_value = fake_now
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_date.today.return_value = date(2026, 5, 26)

        _maybe_run_intraday_scan(broker="simulation")

    scan_selects = [
        c for c in mock_sel.call_args_list
        if len(c[0]) >= 1 and c[0][0] == "b_scan_results"
    ]
    return len(scan_selects) > 0


class TestEntryCutoffB:

    def test_scan_allowed_at_hour_14(self):
        assert _run_scan_at_hour(14) is True

    def test_scan_allowed_at_hour_18(self):
        assert _run_scan_at_hour(18) is True

    def test_scan_blocked_at_hour_19(self):
        assert _run_scan_at_hour(19) is False

    def test_scan_blocked_at_hour_20(self):
        assert _run_scan_at_hour(20) is False

    def test_scan_blocked_at_hour_21(self):
        assert _run_scan_at_hour(21) is False

    def test_cutoff_constant_is_19(self):
        from config.settings import INTRADAY_ENTRY_CUTOFF_UTC
        assert INTRADAY_ENTRY_CUTOFF_UTC == 19
