"""
Daily morning report — sent at 10:30 AM ET after all premarket sessions complete.
Covers Strategy A, B, and C in a single email to amit.thirdeyetrading@gmail.com.

Run: python daily_report.py
Env vars required: SUPABASE_URL, SUPABASE_KEY (B), SUPABASE_URL_C, SUPABASE_KEY_C (C),
                   SUPABASE_URL_A, SUPABASE_KEY_A (A, falls back to B creds if same project),
                   GMAIL_USER, GMAIL_APP_PASSWORD
"""
from __future__ import annotations

import os
import smtplib
from datetime import date, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from supabase import create_client, Client


REPORT_TO = "amit.thirdeyetrading@gmail.com"
TODAY     = date.today().isoformat()


# ── Supabase clients ──────────────────────────────────────────────────────────

def _client(url_var: str, key_var: str) -> Client | None:
    url = os.getenv(url_var)
    key = os.getenv(key_var)
    if not url or not key:
        return None
    return create_client(url, key)


def _sb_a() -> Client | None:
    return _client("SUPABASE_URL_A", "SUPABASE_KEY_A") or _client("SUPABASE_URL", "SUPABASE_KEY")


def _sb_b() -> Client | None:
    return _client("SUPABASE_URL", "SUPABASE_KEY")


def _sb_c() -> Client | None:
    return _client("SUPABASE_URL_C", "SUPABASE_KEY_C")


# ── Data fetchers ─────────────────────────────────────────────────────────────

def _fetch_a() -> dict:
    sb = _sb_a()
    if not sb:
        return {"error": "No credentials (SUPABASE_URL_A / SUPABASE_KEY_A)"}
    try:
        scans   = sb.table("scan_results").select("*").eq("date", TODAY).execute().data or []
        pos_all = sb.table("positions").select("*").execute().data or []
        today_pos = [p for p in pos_all if str(p.get("opened_at") or "").startswith(TODAY)]
        today_closed = [p for p in pos_all if str(p.get("closed_at") or "").startswith(TODAY)]
        open_pos = [p for p in pos_all if p.get("status") == "OPEN"]
        return {
            "scans":        scans,
            "today_pos":    today_pos,
            "today_closed": today_closed,
            "open_pos":     open_pos,
        }
    except Exception as e:
        return {"error": str(e)}


def _fetch_b() -> dict:
    sb = _sb_b()
    if not sb:
        return {"error": "No credentials (SUPABASE_URL / SUPABASE_KEY)"}
    try:
        scans   = sb.table("b_scan_results").select("*").eq("date", TODAY).execute().data or []
        pos_all = sb.table("b_positions").select("*").execute().data or []
        today_pos    = [p for p in pos_all if str(p.get("opened_at") or "").startswith(TODAY)]
        today_closed = [p for p in pos_all if str(p.get("closed_at") or "").startswith(TODAY)]
        open_pos     = [p for p in pos_all if p.get("status") == "OPEN"]
        return {
            "scans":        scans,
            "today_pos":    today_pos,
            "today_closed": today_closed,
            "open_pos":     open_pos,
        }
    except Exception as e:
        return {"error": str(e)}


def _fetch_c() -> dict:
    sb = _sb_c()
    if not sb:
        return {"error": "No credentials (SUPABASE_URL_C / SUPABASE_KEY_C)"}
    try:
        sessions = (
            sb.table("c_sessions").select("*")
            .eq("date", TODAY)
            .neq("is_simulated", True)
            .execute().data or []
        )
        pos_all  = sb.table("c_positions").select("*").eq("open_date", TODAY).execute().data or []
        today_closed = [p for p in pos_all if p.get("status") == "closed"]
        open_pos     = [p for p in pos_all if p.get("status") == "open"]
        return {
            "sessions":     sessions,
            "today_closed": today_closed,
            "open_pos":     open_pos,
        }
    except Exception as e:
        return {"error": str(e)}


# ── Formatters ────────────────────────────────────────────────────────────────

def _pnl_str(v) -> str:
    try:
        f = float(v or 0)
        return f"${f:+.2f}"
    except Exception:
        return "$0.00"


def _section_a(data: dict) -> str:
    if "error" in data:
        return f"STRATEGY A\n  Error fetching data: {data['error']}\n"

    scans  = data["scans"]
    closed = data["today_closed"]
    opens  = data["open_pos"]

    pm = next((s for s in scans if s.get("scan_type") == "premarket"), None)
    failures = [s for s in scans if "failed" in (s.get("scan_type") or "")]

    lines = ["STRATEGY A", "-" * 40]

    if pm:
        r = pm.get("results") or {}
        pipe = r.get("pipeline_counts") or {}
        fg   = (r.get("fear_greed") or {})
        vix  = r.get("vix", "?")
        fb   = r.get("futures_bias", "?")
        ml   = pipe.get("ml_scored", 0)
        pool = pipe.get("post_prefilter", 0)
        vwap = pipe.get("above_vwap", 0)
        fg_v = fg.get("value", "?")
        fg_c = fg.get("classification", "")
        ts   = str(pm.get("created_at") or "")[:16]
        lines.append(f"  Premarket ({ts} UTC): {pool} pool, {vwap} above VWAP, {ml} ML-scored")
        lines.append(f"  Market:  VIX {vix}  Fear&Greed {fg_v} ({fg_c})  Futures {fb}")
        if ml == 0:
            lines.append("  Why no trades: 0 stocks passed ML scoring")
        elif not closed:
            lines.append("  Why no trades: no entries met all criteria")
    else:
        lines.append("  Premarket: did not run")

    if failures:
        for f in failures:
            r = f.get("results") or {}
            lines.append(f"  FAILURE ({f.get('scan_type')}): {r.get('error','')[:120]}")

    net = sum(float(p.get("realized_pnl") or 0) for p in closed)
    if closed:
        lines.append(f"  Closed today ({len(closed)}): net {_pnl_str(net)}")
        for p in closed:
            lines.append(f"    {(p.get('ticker') or '?'):6s} {p.get('close_reason',''):15s} {_pnl_str(p.get('realized_pnl'))}")
    else:
        lines.append("  Positions closed today: none")

    if opens:
        lines.append(f"  Open: {', '.join(p.get('ticker','') for p in opens)}")
    else:
        lines.append("  Open positions: none")

    return "\n".join(lines)


def _section_b(data: dict) -> str:
    if "error" in data:
        return f"STRATEGY B\n  Error fetching data: {data['error']}\n"

    scans  = data["scans"]
    closed = data["today_closed"]
    opens  = data["open_pos"]

    pm       = next((s for s in scans if s.get("scan_type") == "premarket"), None)
    failures = [s for s in scans if "failed" in (s.get("scan_type") or "")]

    lines = ["STRATEGY B", "-" * 40]

    if pm:
        r    = pm.get("results") or {}
        pool = r.get("after_scan", r.get("pool3_count", "?"))
        ts   = str(pm.get("created_at") or "")[:16]
        candidates = r.get("candidate_list", [])
        n_cand = len(candidates) if isinstance(candidates, list) else candidates
        lines.append(f"  Premarket ({ts} UTC): {pool} in pool, {n_cand} candidates after filters")
        if n_cand == 0:
            lines.append("  Why no trades: all candidates filtered out (ORB / top-of-range / pool rules)")
    else:
        lines.append("  Premarket: did not run")

    if failures:
        for f in failures:
            r = f.get("results") or {}
            err = r.get("error", "")
            lines.append(f"  FAILURE ({f.get('scan_type')}): {str(err)[:120]}")

    net = sum(float(p.get("realized_pnl") or 0) for p in closed)
    if closed:
        lines.append(f"  Closed today ({len(closed)}): net {_pnl_str(net)}")
        for p in closed:
            lines.append(f"    {(p.get('ticker') or '?'):6s} {p.get('close_reason',''):15s} {_pnl_str(p.get('realized_pnl'))}")
    else:
        lines.append("  Positions closed today: none")

    if opens:
        lines.append(f"  Open: {', '.join(p.get('ticker','') for p in opens)}")
    else:
        lines.append("  Open positions: none")

    return "\n".join(lines)


def _section_c(data: dict) -> str:
    if "error" in data:
        return f"STRATEGY C\n  Error fetching data: {data['error']}\n"

    sessions = data["sessions"]
    closed   = data["today_closed"]
    opens    = data["open_pos"]

    lines = ["STRATEGY C", "-" * 40]

    pm = next((s for s in sessions if s.get("terminal_reason") not in ("eod_complete", None, "")), None)
    if not pm and sessions:
        pm = sessions[0]

    if pm:
        terminal = pm.get("terminal_reason", "?")
        tickers  = pm.get("tickers_scanned", "?")
        trades   = pm.get("trades_executed", 0)
        cost     = pm.get("total_cost_usd", 0)
        ts       = str(pm.get("started_at") or "")[:16]
        lines.append(f"  Premarket ({ts} UTC): terminal={terminal}  tickers={tickers}  cost=${cost:.3f}")
        if terminal == "no_viable_proposals":
            lines.append("  Why no trades: AI agents evaluated all candidates and rejected (risk/quality gates)")
        elif terminal == "no_opportunity":
            lines.append("  Why no trades: market agent found no scan candidates")
        elif terminal == "error":
            lines.append("  Why no trades: premarket session errored — check logs")
        elif trades and int(trades) > 0:
            lines.append(f"  Premarket trades placed: {trades}")
    else:
        lines.append("  Premarket: did not run today")

    net = sum(float(p.get("realized_pnl") or 0) for p in closed)
    if closed:
        lines.append(f"  Closed today ({len(closed)}): net {_pnl_str(net)}")
        for p in closed:
            lines.append(f"    {(p.get('ticker') or '?'):6s} {p.get('close_reason',''):15s} {_pnl_str(p.get('realized_pnl'))}")
    else:
        lines.append("  Positions closed today: none")

    if opens:
        lines.append(f"  Open: {', '.join(p.get('ticker','') for p in opens)}")
    else:
        lines.append("  Open positions: none")

    return "\n".join(lines)


# ── Email sender ──────────────────────────────────────────────────────────────

def _send(subject: str, body: str) -> None:
    gmail_user = os.environ["GMAIL_USER"]
    gmail_pass = os.environ["GMAIL_APP_PASSWORD"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = gmail_user
    msg["To"]      = REPORT_TO
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(gmail_user, gmail_pass)
        s.send_message(msg)
    print(f"Report sent to {REPORT_TO}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    now_utc = datetime.now(timezone.utc).strftime("%H:%M UTC")
    print(f"[daily_report] Building report for {TODAY} at {now_utc}")

    data_a = _fetch_a()
    data_b = _fetch_b()
    data_c = _fetch_c()

    sec_a = _section_a(data_a)
    sec_b = _section_b(data_b)
    sec_c = _section_c(data_c)

    # Headline flags for subject line
    def _flag(data, key="scans"):
        if "error" in data:
            return "ERR"
        failures = [s for s in (data.get(key) or data.get("sessions") or [])
                    if "failed" in (s.get("scan_type") or s.get("terminal_reason") or "")]
        closed = data.get("today_closed") or []
        if failures:
            return "FAIL"
        if closed:
            net = sum(float(p.get("realized_pnl") or 0) for p in closed)
            return f"{'+'if net >= 0 else ''}{net:.0f}"
        return "no trades"

    subj = (f"Trading Report {TODAY}  |  "
            f"A: {_flag(data_a)}  B: {_flag(data_b)}  C: {_flag(data_c, 'sessions')}")

    body = "\n\n".join([
        f"Trading Report — {TODAY}  ({now_utc})",
        "=" * 50,
        sec_a,
        sec_b,
        sec_c,
        "=" * 50,
        "Generated by daily_report.py — trading-agent-b",
    ])

    _send(subj, body)


if __name__ == "__main__":
    main()
