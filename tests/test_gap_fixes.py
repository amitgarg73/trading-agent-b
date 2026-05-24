"""
Tests for Gap 1 + Gap 8 fixes in Strategy B.

Gap 1 — strategy.select_trades() retry on Anthropic API errors
Gap 8 — eod() pool scoring failures are logged, not silently swallowed
"""
import pytest
from unittest.mock import patch, MagicMock, call
import anthropic


# ── Gap 1: select_trades retry ────────────────────────────────────────────────

def _make_resp(trades: list) -> MagicMock:
    import json
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps({
        "trades": trades, "summary": "ok", "pass": True,
    }))]
    return resp


def _make_candidate() -> dict:
    return {
        "ticker": "JNJ", "total_score": 7, "action": "BUY",
        "current_price": 155.0, "vol_ratio": 1.2,
    }


class TestSelectTradesRetry:

    def test_success_first_attempt(self):
        """Happy path — no retry needed."""
        from agents.strategy import select_trades
        with patch("agents.strategy.client") as mc:
            mc.messages.create.return_value = _make_resp([
                {"ticker": "JNJ", "action": "BUY", "entry_price": 155.0,
                 "target_price": 157.3, "stop_loss": 153.9,
                 "shares": 20, "confidence": "MEDIUM",
                 "position_size": 3100, "estimated_profit": 46,
                 "strategy": "stratb"}
            ])
            result = select_trades([_make_candidate()], {}, [])

        assert len(result["trades"]) == 1
        assert mc.messages.create.call_count == 1

    def test_retry_on_connection_error_then_success(self):
        """APIConnectionError on first call → retries and returns trades on second."""
        from agents.strategy import select_trades
        with patch("agents.strategy.client") as mc, \
             patch("agents.strategy.time.sleep"):
            mc.messages.create.side_effect = [
                anthropic.APIConnectionError(request=MagicMock()),
                _make_resp([{"ticker": "JNJ", "action": "BUY",
                             "entry_price": 155.0, "target_price": 157.3,
                             "stop_loss": 153.9, "shares": 20,
                             "confidence": "MEDIUM", "position_size": 3100,
                             "estimated_profit": 46, "strategy": "stratb"}]),
            ]
            result = select_trades([_make_candidate()], {}, [])

        assert len(result["trades"]) == 1
        assert mc.messages.create.call_count == 2

    def test_retry_on_rate_limit(self):
        """RateLimitError triggers retry."""
        from agents.strategy import select_trades
        with patch("agents.strategy.client") as mc, \
             patch("agents.strategy.time.sleep"):
            mc.messages.create.side_effect = [
                anthropic.RateLimitError(
                    message="rate limit",
                    response=MagicMock(status_code=429),
                    body={},
                ),
                _make_resp([]),
            ]
            result = select_trades([_make_candidate()], {}, [])

        assert mc.messages.create.call_count == 2

    def test_all_attempts_fail_returns_empty_no_crash(self):
        """3 consecutive failures → returns empty trades dict, does not raise."""
        from agents.strategy import select_trades
        with patch("agents.strategy.client") as mc, \
             patch("agents.strategy.time.sleep"):
            mc.messages.create.side_effect = anthropic.APIConnectionError(
                request=MagicMock()
            )
            result = select_trades([_make_candidate()], {}, [])

        assert result["trades"] == []
        assert mc.messages.create.call_count == 3

    def test_all_attempts_fail_internal_server(self):
        """InternalServerError retried 3 times then returns empty."""
        from agents.strategy import select_trades
        with patch("agents.strategy.client") as mc, \
             patch("agents.strategy.time.sleep"):
            mc.messages.create.side_effect = anthropic.InternalServerError(
                message="500", response=MagicMock(status_code=500), body={},
            )
            result = select_trades([_make_candidate()], {}, [])

        assert result["trades"] == []
        assert mc.messages.create.call_count == 3

    def test_backoff_sleep_durations(self):
        """Sleep durations follow 15 * attempt: 15s, 30s between attempts."""
        from agents.strategy import select_trades
        with patch("agents.strategy.client") as mc, \
             patch("agents.strategy.time.sleep") as mock_sleep:
            mc.messages.create.side_effect = [
                anthropic.APIConnectionError(request=MagicMock()),
                anthropic.APIConnectionError(request=MagicMock()),
                _make_resp([]),
            ]
            select_trades([_make_candidate()], {}, [])

        waits = [c.args[0] for c in mock_sleep.call_args_list]
        assert waits == [15, 30], f"Expected [15, 30], got {waits}"

    def test_empty_candidates_skips_api(self):
        """No candidates → returns immediately without calling API."""
        from agents.strategy import select_trades
        with patch("agents.strategy.client") as mc:
            result = select_trades([], {}, [])

        mc.messages.create.assert_not_called()
        assert result["trades"] == []


# ── Gap 8: EOD pool scoring non-silent failure ────────────────────────────────

class TestEodPoolScoringNonSilentFailure:

    def _run_eod(self, score_side_effect=None, write_side_effect=None):
        """
        Run orchestrator.eod() with all external calls mocked.
        Returns (score_called, write_called, printed_output).
        """
        import io, sys
        from unittest.mock import patch, MagicMock

        captured = io.StringIO()

        scoring_result = {"scored": 5, "promoted": 1, "demoted": 0}

        with patch("orchestrator.db") as mock_db, \
             patch("orchestrator._is_halted", return_value=False), \
             patch("orchestrator.open_positions", return_value=[]), \
             patch("orchestrator.close_all_positions", return_value=[]), \
             patch("orchestrator.score_today",
                   side_effect=score_side_effect or [scoring_result]) as mock_score, \
             patch("orchestrator.write_daily_performance",
                   side_effect=write_side_effect or [None]) as mock_write, \
             patch("sys.stdout", captured):
            mock_db.select.return_value = []
            mock_db.insert.return_value = {}
            from orchestrator import eod
            eod(broker="simulation")

        return mock_score, mock_write, captured.getvalue()

    def test_eod_happy_path_calls_both(self):
        """Normal EOD: score_today and write_daily_performance are both called."""
        mock_score, mock_write, _ = self._run_eod()
        mock_score.assert_called_once()
        mock_write.assert_called_once()

    def test_score_today_failure_does_not_crash_eod(self):
        """score_today raising an exception → EOD continues, write_daily_performance still runs."""
        mock_score, mock_write, output = self._run_eod(
            score_side_effect=Exception("DB timeout")
        )
        mock_score.assert_called_once()
        mock_write.assert_called_once()  # must still run after score fails
        assert "Pool scorer failed" in output or "stale scores" in output

    def test_write_daily_performance_failure_does_not_crash_eod(self):
        """write_daily_performance raising an exception → EOD completes, warning printed."""
        mock_score, mock_write, output = self._run_eod(
            write_side_effect=Exception("Supabase write failed")
        )
        mock_score.assert_called_once()
        mock_write.assert_called_once()
        assert "write_daily_performance failed" in output or "dashboard" in output

    def test_both_fail_eod_still_completes(self):
        """Both scoring steps fail → EOD still completes (positions already closed)."""
        mock_score, mock_write, output = self._run_eod(
            score_side_effect=Exception("score error"),
            write_side_effect=Exception("write error"),
        )
        # Both were attempted
        mock_score.assert_called_once()
        mock_write.assert_called_once()
        # Warnings printed for both
        assert "Pool scorer failed" in output or "stale scores" in output
        assert "write_daily_performance failed" in output or "dashboard" in output
