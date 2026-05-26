"""
Strategy B Dashboard — Streamlit app.

Page 0: Summary — KPIs, in-flight positions, trade plan, heatmap
Page 1: Today — intraday detail with market context, watermarks, 7-day sparkline
Page 2: Performance — Agent Scorecard, charts, exit reasons, integrity
Page 3: Strategy B — pools, scores, P&L history
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import date, datetime, timedelta
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _key in ["ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_KEY", "DASHBOARD_PASSWORD"]:
    if _key in st.secrets:
        os.environ[_key] = st.secrets[_key]

from core import db
from config.settings import (
    DASHBOARD_PASSWORD, TOTAL_CAPITAL, DAILY_PROFIT_TARGET,
    DAILY_LOCK_IN_TARGET, DAILY_BONUS_TARGET,
    MIN_REWARD_RISK, MIN_POSITION_PCT, MAX_POSITION_PCT, DAILY_LOSS_LIMIT,
)
from config.blue_chips import POOL_2_SEED, SECTOR_MAP

st.set_page_config(page_title="Trading Agent B", page_icon="📊", layout="wide")

st.markdown("<style>h3 { color: #FAFAFA !important; }</style>", unsafe_allow_html=True)

# --- Auth ---
def _check_password() -> bool:
    if st.session_state.get("authenticated"):
        return True
    pwd = st.sidebar.text_input("Password", type="password")
    if st.sidebar.button("Login"):
        if pwd == DASHBOARD_PASSWORD:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.sidebar.error("Wrong password")
    return False

if not _check_password():
    st.stop()

# --- Navigation ---
page = st.sidebar.radio("View", ["Summary", "Today", "Performance", "Strategy B"])


def _fmt_pnl(v: float) -> str:
    return f"+${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"


def _pnl_color(v: float) -> str:
    return "#2ecc71" if v > 0 else "#e74c3c" if v < 0 else "#95a5a6"


def _compute_metrics_b(perf_rows: list[dict], positions: list[dict]) -> dict:
    """Compute Agent Scorecard metrics from b_daily_performance + b_positions."""
    if not perf_rows:
        return {}

    total_rows   = [r for r in perf_rows if r.get("pool") is None]
    if not total_rows:
        return {}

    days         = len(total_rows)
    avg_pnl      = sum(r.get("gross_pnl", 0) or 0 for r in total_rows) / days
    win_days     = sum(1 for r in total_rows if (r.get("gross_pnl", 0) or 0) > 0)
    avg_wr       = sum((r.get("win_rate", 0) or 0) * 100 for r in total_rows) / days

    closed = [p for p in positions if p.get("status") == "CLOSED"
              and p.get("close_reason") not in ("UNFILLED", "CLEANUP")]
    wins_t   = [p for p in closed if (p.get("realized_pnl") or 0) > 0]
    losses_t = [p for p in closed if (p.get("realized_pnl") or 0) <= 0]
    avg_win  = sum(p.get("realized_pnl", 0) or 0 for p in wins_t) / len(wins_t) if wins_t else 0
    avg_loss = abs(sum(p.get("realized_pnl", 0) or 0 for p in losses_t) / len(losses_t)) if losses_t else 0
    actual_rr = round(avg_win / avg_loss, 2) if avg_loss else 0

    # Close reason breakdown
    close_reasons: dict[str, int] = {}
    for p in closed:
        cr = p.get("close_reason") or p.get("exit_mechanism") or "UNKNOWN"
        close_reasons[cr] = close_reasons.get(cr, 0) + 1

    # Best / worst trade in window
    pnl_vals = [(p.get("realized_pnl") or 0, p.get("ticker", "?")) for p in closed]
    best_pnl, best_ticker   = max(pnl_vals, key=lambda x: x[0]) if pnl_vals else (0, None)
    worst_pnl, worst_ticker = min(pnl_vals, key=lambda x: x[0]) if pnl_vals else (0, None)

    # Confidence cohort
    conf_stats: dict[str, dict] = {}
    for level in ("HIGH", "MEDIUM", "LOW"):
        cohort = [p for p in closed if (p.get("confidence") or "").upper() == level]
        if cohort:
            c_wins = [p for p in cohort if (p.get("realized_pnl") or 0) > 0]
            c_pnl  = sum(p.get("realized_pnl", 0) or 0 for p in cohort)
            conf_stats[level] = {
                "count":    len(cohort),
                "win_rate": len(c_wins) / len(cohort) * 100,
                "avg_pnl":  c_pnl / len(cohort),
                "total_pnl": c_pnl,
            }

    # Integrity
    date_set    = {str(r["date"])[:10] for r in total_rows}
    orphaned    = [p for p in positions if p.get("status") == "OPEN"
                   and str(p.get("date") or "")[:10] not in {str(date.today())[:10]}]
    seen: dict[str, set] = {}
    dup_count = 0
    for p in closed:
        d = str(p.get("date") or "")[:10]
        seen.setdefault(d, set())
        if p["ticker"] in seen[d]:
            dup_count += 1
        seen[d].add(p["ticker"])

    rr_violations = [
        {"ticker": p["ticker"], "rr": round(
            (float(p["target_price"]) - float(p["entry_price"])) /
            max(float(p["entry_price"]) - float(p["stop_loss"]), 0.0001), 2
        )}
        for p in closed
        if p.get("target_price") and p.get("stop_loss") and p.get("entry_price")
        and (float(p["target_price"]) - float(p["entry_price"])) /
            max(float(p["entry_price"]) - float(p["stop_loss"]), 0.0001) < MIN_REWARD_RISK
    ]
    size_violations = [
        {"ticker": p["ticker"], "size": p.get("position_size")}
        for p in closed
        if p.get("position_size") and not (
            MIN_POSITION_PCT * TOTAL_CAPITAL <= float(p["position_size"]) <= MAX_POSITION_PCT * TOTAL_CAPITAL
        )
    ]

    unfilled_count  = sum(1 for p in positions if p.get("close_reason") == "UNFILLED"
                          and str(p.get("date") or "")[:10] in date_set)
    total_attempted = len(closed) + unfilled_count

    loss_limit_days = sum(1 for r in total_rows if (r.get("gross_pnl", 0) or 0) < (DAILY_LOSS_LIMIT or -500))
    lock_in_days    = sum(1 for r in total_rows if (r.get("gross_pnl", 0) or 0) >= DAILY_LOCK_IN_TARGET)

    # Friction gap
    gap_rows = [r for r in total_rows if r.get("friction_gap") is not None]

    # Grade
    pnl_score = min(avg_pnl / DAILY_PROFIT_TARGET * 40, 40) if DAILY_PROFIT_TARGET else 0
    wd_score  = win_days / days * 30 if days else 0
    wr_score  = avg_wr / 100 * 30
    score     = pnl_score + wd_score + wr_score
    grade     = "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D"

    return dict(
        days=days, avg_daily_pnl=avg_pnl, win_days=win_days, avg_win_rate=avg_wr,
        actual_rr=actual_rr, close_reasons=close_reasons,
        best_ticker=best_ticker, best_pnl=best_pnl,
        worst_ticker=worst_ticker, worst_pnl=worst_pnl,
        confidence_stats=conf_stats,
        orphaned=orphaned, duplicate_count=dup_count,
        rr_violations=rr_violations, size_violations=size_violations,
        unfilled_count=unfilled_count, total_attempted=total_attempted,
        loss_limit_days=loss_limit_days, lock_in_days=lock_in_days,
        gap_rows=gap_rows, grade=grade, score=round(score, 1),
    )


def _show_runs_table_b(run_date: str, open_positions: list, closed_positions: list):
    """Render per-run P&L breakdown for Strategy B. Silently skips if b_daily_runs table doesn't exist yet."""
    try:
        runs = db.select("b_daily_runs", filters={"date": run_date})
    except Exception:
        return
    if not runs:
        return

    runs.sort(key=lambda r: r.get("run_number", 0))
    st.subheader("📊 Runs Today")

    run_rows = []
    for run in runs:
        run_id    = run["id"]
        rp_open   = [p for p in open_positions   if p.get("run_id") == run_id]
        rp_closed = [p for p in closed_positions  if p.get("run_id") == run_id]
        r_real    = sum(p.get("realized_pnl", 0) or 0 for p in rp_closed)
        r_unreal  = sum(p.get("unrealized_pnl", 0) or 0 for p in rp_open)
        r_anticip = sum(
            round((float(p.get("target_price") or 0) - float(p.get("entry_price") or 0))
                  * int(p.get("shares") or 0), 2)
            for p in rp_open
        )
        r_net = r_real + r_unreal

        started = run.get("started_at") or ""
        try:
            started_str = datetime.fromisoformat(started.replace("Z", "+00:00")).strftime("%H:%M UTC")
        except Exception:
            started_str = "—"

        run_label = "Premarket" if run.get("run_type") == "premarket" else f"Intraday #{run.get('run_number')}"

        run_rows.append({
            "Run":         run_label,
            "Started":     started_str,
            "# Opened":    run.get("positions_opened", 0),
            "Realized":    _fmt_pnl(r_real)     if rp_closed              else "—",
            "Unrealized":  _fmt_pnl(r_unreal)   if rp_open                else "—",
            "Anticipated": f"${r_anticip:,.0f}" if rp_open and r_anticip  else "—",
            "Net P&L":     _fmt_pnl(r_net)      if (rp_open or rp_closed) else "—",
        })

    st.dataframe(pd.DataFrame(run_rows), use_container_width=True, hide_index=True)

    legacy = [p for p in open_positions + closed_positions if not p.get("run_id")]
    if legacy:
        st.caption(f"ℹ️ {len(legacy)} position(s) have no run assigned (opened before run tracking).")


# ============================================================
# PAGE 0: Summary
# ============================================================
if page == "Summary":
    today_str = str(date.today())

    plans  = db.select("b_trade_plans", filters={"date": today_str}, limit=1)
    plan   = plans[0] if plans else None
    trades = [t for t in (db.select("b_planned_trades", filters={"plan_id": plan["id"]}) if plan else [])
              if t.get("status") != "CANCELLED"]

    all_open   = db.select("b_positions", filters={"status": "OPEN"})
    all_closed = db.select("b_positions", filters={"status": "CLOSED"})
    today_closed = [
        p for p in all_closed
        if str(p.get("closed_at", ""))[:10] == today_str
        and p.get("close_reason") not in ("CLEANUP",)
    ]

    pos_by_ticker: dict[str, dict] = {}
    for pos in all_open + today_closed:
        pos_by_ticker[pos["ticker"]] = pos

    def _sum_status(ticker: str):
        pos = pos_by_ticker.get(ticker)
        if pos is None:
            return "⏳ Pending", 0.0
        if pos["status"] == "OPEN":
            return "🟢 In Flight", float(pos.get("unrealized_pnl") or 0)
        reason = (pos.get("close_reason") or "").upper()
        pnl = float(pos.get("realized_pnl") or 0)
        if reason == "TARGET":       return "✅ Target",  pnl
        if reason == "BONUS_TARGET": return "🎯 Bonus",   pnl
        if reason == "STOP":         return "🛑 Stop",    pnl
        if reason == "MANUAL_TRAIL": return "📉 Trail",   pnl
        if reason == "EOD":          return "⏰ EOD",     pnl
        return f"⚫ {reason}", pnl

    # --- Financials ---
    realized   = sum(p.get("realized_pnl", 0) or 0 for p in today_closed)
    unrealized = sum(p.get("unrealized_pnl", 0) or 0 for p in all_open)
    total_pnl  = realized + unrealized
    anticipated = sum(t.get("estimated_profit", 0) or 0 for t in trades
                      if _sum_status(t["ticker"])[0] != "⏳ Pending")

    all_perf = db.select("b_daily_performance", order="date")
    all_perf_total = [r for r in all_perf if r.get("pool") is None]
    cumulative_pnl = sum(r.get("gross_pnl", 0) or 0 for r in all_perf_total)
    current_capital = TOTAL_CAPITAL + cumulative_pnl
    pct_return = total_pnl / TOTAL_CAPITAL * 100

    won  = [p for p in today_closed if (p.get("realized_pnl") or 0) > 0]
    lost = [p for p in today_closed if (p.get("realized_pnl") or 0) <= 0]
    win_rate = len(won) / len(today_closed) * 100 if today_closed else 0

    executed_trades = [t for t in trades if _sum_status(t["ticker"])[0] != "⏳ Pending"]

    # --- Header ---
    if plan is None:
        badge, badge_color = "PENDING", "#7f8c8d"
    elif plan.get("status") == "HALTED":
        badge, badge_color = "HALTED", "#c0392b"
    else:
        badge, badge_color = "TRADING", "#27ae60"

    h1, h2 = st.columns([4, 1])
    h1.title(f"Strategy B — Summary — {today_str}")
    h2.markdown(
        f"<div style='text-align:right;padding-top:14px'>"
        f"<span style='background:{badge_color};color:white;padding:6px 14px;"
        f"border-radius:6px;font-weight:bold;font-size:16px'>{badge}</span></div>",
        unsafe_allow_html=True,
    )
    if plan and plan.get("status") == "HALTED":
        st.error(f"🛑 Halted — {plan.get('risk_note', '')}")

    st.divider()

    # --- KPI row ---
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Capital", f"${current_capital:,.0f}",
              delta=f"{cumulative_pnl:+,.0f} all-time" if cumulative_pnl != 0 else None,
              delta_color="normal")
    k2.metric("Today P&L", _fmt_pnl(total_pnl),
              delta=f"{pct_return:+.2f}% return",
              delta_color="normal" if total_pnl >= 0 else "inverse")
    k3.metric("Realized",   _fmt_pnl(realized))
    k4.metric("Unrealized", _fmt_pnl(unrealized))
    k5.metric("Anticipated", f"${anticipated:,.0f}",
              delta=f"{anticipated/DAILY_PROFIT_TARGET*100:.0f}% of ${DAILY_PROFIT_TARGET:,} target" if anticipated else None,
              delta_color="normal" if anticipated >= DAILY_PROFIT_TARGET else "inverse")
    k6.metric("% Return", f"{pct_return:+.2f}%")

    st.divider()

    # --- Trade stats row ---
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Open Positions", len(all_open))
    t2.metric("Closed Today",   len(today_closed))
    t3.metric("Win Rate", f"{win_rate:.0f}%" if today_closed else "—",
              delta=f"{len(won)}W / {len(lost)}L" if today_closed else None,
              delta_color="off")
    t4.metric("Trades Executed", len(executed_trades))

    # ── Runs Today ────────────────────────────────────────────────
    _show_runs_table_b(today_str, all_open, today_closed)

    st.divider()

    # --- In-flight positions ---
    st.subheader(f"🟢 In Flight — {len(all_open)} position{'s' if len(all_open) != 1 else ''}")
    if all_open:
        for pos in all_open:
            ticker   = pos["ticker"]
            pool_num = pos.get("pool", "?")
            entry    = float(pos.get("entry_price") or 0)
            current  = float(pos.get("current_price") or entry)
            target   = float(pos.get("target_price") or entry)
            stop     = float(pos.get("stop_loss") or 0)
            pnl      = float(pos.get("unrealized_pnl") or 0)
            icon     = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            pool_badge = (
                f"<span style='background:#2980b9;color:white;padding:2px 8px;"
                f"border-radius:4px;font-size:12px'>P{pool_num}</span>"
            )
            sector = SECTOR_MAP.get(ticker, "")
            label  = f"{ticker} · {sector}" if sector else ticker

            c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 3, 2])
            c1.markdown(f"**{icon} {label}** {pool_badge}", unsafe_allow_html=True)
            c2.markdown(f"Entry: **${entry:.2f}**")
            c3.markdown(f"Now: **${current:.2f}**")
            c4.markdown(f"Target ${target:.2f}  ·  Stop ${stop:.2f}")
            c5.markdown(
                f"<span style='color:{_pnl_color(pnl)};font-weight:bold;font-size:16px'>"
                f"{_fmt_pnl(pnl)}</span>",
                unsafe_allow_html=True,
            )
    else:
        st.markdown("No open positions right now.")

    st.divider()

    # --- Today's plan (executed only) ---
    st.subheader(f"📋 Today's Plan — {len(executed_trades)} trade{'s' if len(executed_trades) != 1 else ''} executed")
    if executed_trades:
        conf_icon = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}
        plan_rows = []
        for t in executed_trades:
            status_label, pnl_val = _sum_status(t["ticker"])
            plan_rows.append({
                "Status":     status_label,
                "Pool":       f"P{t.get('pool', '?')}",
                "Ticker":     t["ticker"],
                "Sector":     SECTOR_MAP.get(t["ticker"], ""),
                "Conf.":      f"{conf_icon.get(t.get('confidence',''), '⚪')} {t.get('confidence','')}",
                "Entry":      f"${float(t.get('entry_price') or 0):,.2f}",
                "Target":     f"${float(t.get('target_price') or 0):,.2f}",
                "Stop":       f"${float(t.get('stop_loss') or 0):,.2f}",
                "Size":       f"${float(t.get('position_size') or 0):,.0f}",
                "Est. P&L":   f"${float(t.get('estimated_profit') or 0):,.0f}",
                "Actual P&L": _fmt_pnl(pnl_val) if pnl_val != 0 else "—",
            })
        st.dataframe(pd.DataFrame(plan_rows), use_container_width=True, hide_index=True)

        with st.expander("💬 Claude's Reasoning"):
            for t in executed_trades:
                conf_clr = "green" if t.get("confidence") == "HIGH" else (
                           "orange" if t.get("confidence") == "MEDIUM" else "gray")
                st.markdown(
                    f"**{t['ticker']}** — "
                    f"<span style='color:{conf_clr};font-weight:bold'>{t.get('confidence','')}</span>: "
                    f"{t.get('reasoning') or '—'}",
                    unsafe_allow_html=True,
                )
    elif trades:
        st.info(f"Plan ready — {len(trades)} trades selected, waiting for market open.")
    else:
        st.markdown("No trade plan yet.")



# ============================================================
# PAGE 1: Today — Daily Summary
# ============================================================
elif page == "Today":
    today_str = str(date.today())

    # Fetch today's plan and trades
    plans = db.select("b_trade_plans", filters={"date": today_str}, limit=1)
    plan  = plans[0] if plans else None

    trades = []
    if plan:
        trades = [t for t in db.select("b_planned_trades", filters={"plan_id": plan["id"]})
                  if t.get("status") != "CANCELLED"]

    open_pos     = db.select("b_positions", filters={"status": "OPEN"})
    all_closed   = db.select("b_positions", filters={"status": "CLOSED"})
    today_closed = [
        p for p in all_closed
        if str(p.get("closed_at", ""))[:10] == today_str
        and p.get("close_reason") not in ("CLEANUP",)
    ]

    # Position lookup by ticker (planned_trade_id not always written)
    pos_by_ticker: dict[str, dict] = {}
    for pos in open_pos + today_closed:
        pos_by_ticker[pos["ticker"]] = pos

    def _trade_status(ticker: str):
        """Return (status_label, actual_pnl) for a planned trade ticker."""
        pos = pos_by_ticker.get(ticker)
        if pos is None:
            return "⏳ Pending", 0.0
        if pos["status"] == "OPEN":
            return "🔵 Open", float(pos.get("unrealized_pnl") or 0)
        reason = (pos.get("close_reason") or "").upper()
        pnl = float(pos.get("realized_pnl") or 0)
        if reason == "TARGET":       return "✅ Target",  pnl
        if reason == "BONUS_TARGET": return "🎯 Bonus",   pnl
        if reason == "STOP":         return "🛑 Stop",    pnl
        if reason == "MANUAL_TRAIL": return "📉 Trail",   pnl
        if reason == "EOD":          return "🕐 EOD",     pnl
        return f"⚫ {reason}", pnl

    # --- Status badge ---
    if plan is None:
        badge, badge_color = "PENDING", "#7f8c8d"
    elif plan.get("status") == "HALTED":
        badge, badge_color = "HALTED", "#c0392b"
    else:
        badge, badge_color = "TRADING", "#27ae60"

    h1, h2 = st.columns([4, 1])
    h1.title(f"Strategy B — {today_str}")
    h2.markdown(
        f"<div style='text-align:right;padding-top:14px'>"
        f"<span style='background:{badge_color};color:white;padding:6px 14px;"
        f"border-radius:6px;font-weight:bold;font-size:16px'>{badge}</span></div>",
        unsafe_allow_html=True,
    )
    if plan and plan.get("status") == "HALTED":
        st.error(f"🛑 Trading halted today — {plan.get('risk_note', '')}")

    st.divider()

    # --- Market context ---
    today_perf_rows = db.select("b_daily_performance", filters={"date": today_str})
    today_perf = next((r for r in today_perf_rows if r.get("pool") is None), None)
    if today_perf:
        vix     = today_perf.get("vix_level")
        fg      = today_perf.get("fear_greed")
        spy_chg = today_perf.get("spy_change_pct")
        regime  = today_perf.get("regime_label") or "—"
        regime_colors = {
            "TREND":    "#27ae60", "CHOPPY": "#e67e22",
            "HIGH_VOL": "#e74c3c", "FEAR":   "#8e44ad",
        }
        rc = regime_colors.get(regime, "#7f8c8d")
        spy_str = f"{spy_chg:+.2f}%" if spy_chg is not None else "—"
        st.markdown(
            f"**Market Context:** "
            f"VIX `{vix or '—'}` | Fear & Greed `{fg or '—'}` | "
            f"SPY `{spy_str}` | "
            f"Regime <span style='background:{rc};color:white;padding:2px 8px;"
            f"border-radius:4px;font-weight:bold'>{regime}</span>",
            unsafe_allow_html=True,
        )
        st.divider()

    # --- KPI row ---
    realized   = sum(p.get("realized_pnl", 0) or 0 for p in today_closed)
    unrealized = sum(p.get("unrealized_pnl", 0) or 0 for p in open_pos)
    total_pnl  = realized + unrealized
    wins       = [p for p in today_closed if (p.get("realized_pnl") or 0) > 0]
    win_rate   = len(wins) / len(today_closed) * 100 if today_closed else 0
    pool3      = plan.get("pool3_tickers") or [] if plan else []

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Today P&L",      _fmt_pnl(total_pnl),
              delta=f"{total_pnl/50_000*100:+.2f}% of capital",
              delta_color="normal" if total_pnl >= 0 else "inverse")
    k2.metric("Realized",        _fmt_pnl(realized))
    k3.metric("Unrealized",      _fmt_pnl(unrealized))
    k4.metric("Win Rate",        f"{win_rate:.0f}%" if today_closed else "—",
              delta=f"{len(wins)}W / {len(today_closed)-len(wins)}L" if today_closed else None,
              delta_color="off")
    k5.metric("Open Positions",  len(open_pos))
    k6.metric("Pool 3 Picks",    len(pool3))

    st.divider()

    # --- Pool 3 tickers — color-coded cards ---
    if pool3:
        st.subheader("Today's Pool 3")
        open_tickers   = {p["ticker"] for p in open_pos}
        closed_tickers = {p["ticker"] for p in today_closed}
        cols = st.columns(5)
        for i, t in enumerate(pool3):
            sector = SECTOR_MAP.get(t, "—")
            if t in open_tickers:
                pos    = pos_by_ticker.get(t, {})
                unreal = float(pos.get("unrealized_pnl") or 0)
                pnl_str = _fmt_pnl(unreal)
                pnl_clr = "#2ecc71" if unreal >= 0 else "#e74c3c"
                badge   = f"<div style='font-size:11px;color:{pnl_clr};margin-top:2px'>{pnl_str} open</div>"
                bg      = "#1a3a2a"
                border  = "#2ecc71"
            elif t in closed_tickers:
                pos  = pos_by_ticker.get(t, {})
                real = float(pos.get("realized_pnl") or 0)
                pnl_str = _fmt_pnl(real)
                pnl_clr = "#2ecc71" if real >= 0 else "#e74c3c"
                badge   = f"<div style='font-size:11px;color:{pnl_clr};margin-top:2px'>{pnl_str} closed</div>"
                bg      = "#1a1a2e"
                border  = "#7f8c8d"
            else:
                badge  = ""
                bg     = "#1c1c1c"
                border = "#333"
            cols[i % 5].markdown(
                f"<div style='background:{bg};border:1px solid {border};border-radius:8px;"
                f"padding:10px 12px;margin-bottom:8px;min-height:64px'>"
                f"<div style='font-size:15px;font-weight:700;color:#fff'>{t}</div>"
                f"<div style='font-size:11px;color:#888;margin-top:1px'>{sector}</div>"
                f"{badge}</div>",
                unsafe_allow_html=True,
            )
        st.divider()

    # --- Trade plan table (executed trades only — no pending) ---
    executed_trades = [t for t in trades if _trade_status(t["ticker"])[0] != "⏳ Pending"]
    if trades and not executed_trades:
        st.info(f"Plan ready ({len(trades)} trades selected) — waiting for market open to execute")
    if executed_trades:
        st.subheader(f"Today's Trade Plan — {len(executed_trades)} executed")
        conf_icon = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}
        rows = []
        for t in executed_trades:
            status_label, actual_pnl = _trade_status(t["ticker"])
            rows.append({
                "Pool":        f"P{t.get('pool', '?')}",
                "Ticker":      t["ticker"],
                "Conf":        f"{conf_icon.get(t.get('confidence',''), '⚪')} {t.get('confidence','')}",
                "Entry":       f"${float(t.get('entry_price') or 0):,.2f}",
                "Target":      f"${float(t.get('target_price') or 0):,.2f}",
                "Stop":        f"${float(t.get('stop_loss') or 0):,.2f}",
                "Shares":      t.get("shares", "—"),
                "Est. Profit": f"${float(t.get('estimated_profit') or 0):,.0f}",
                "Status":      status_label,
                "Actual P&L":  _fmt_pnl(actual_pnl) if actual_pnl != 0 else "—",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # --- Claude's reasoning ---
        with st.expander("💬 Claude's Reasoning per Trade"):
            for t in executed_trades:
                confidence = t.get("confidence", "")
                conf_clr   = "green" if confidence == "HIGH" else ("orange" if confidence == "MEDIUM" else "gray")
                reasoning  = t.get("reasoning") or "No reasoning recorded"
                st.markdown(
                    f"**{t['ticker']}** — "
                    f"<span style='color:{conf_clr};font-weight:bold'>{confidence}</span>: {reasoning}",
                    unsafe_allow_html=True,
                )

        st.divider()

    # --- Position Heatmap ---
    all_today_pos = open_pos + today_closed
    if all_today_pos:
        st.subheader("Position Heatmap")
        hm_labels, hm_size, hm_pnl, hm_text, hm_hover = [], [], [], [], []
        for pos in all_today_pos:
            ticker   = pos["ticker"]
            pos_size = float(pos.get("position_size") or 1)
            pnl      = (float(pos.get("unrealized_pnl") or 0) if pos["status"] == "OPEN"
                        else float(pos.get("realized_pnl") or 0))
            status   = pos.get("status", "")
            hm_labels.append(ticker)
            hm_size.append(max(pos_size, 1))
            hm_pnl.append(pnl)
            hm_text.append(f"{ticker}\n{_fmt_pnl(pnl)}")
            hm_hover.append(f"{ticker} | Pool {pos.get('pool','?')} | {status}<br>P&L: {_fmt_pnl(pnl)}")

        fig_hm = go.Figure(go.Treemap(
            labels=hm_labels,
            parents=[""] * len(hm_labels),
            values=hm_size,
            text=hm_text,
            hovertemplate="%{customdata}<extra></extra>",
            customdata=hm_hover,
            textinfo="text",
            marker=dict(
                colors=hm_pnl,
                colorscale=[
                    [0.0, "#c0392b"], [0.45, "#e74c3c"],
                    [0.5,  "#95a5a6"],
                    [0.55, "#27ae60"], [1.0,  "#1e8449"],
                ],
                cmid=0, showscale=True,
                colorbar=dict(title="P&L ($)", thickness=12),
            ),
        ))
        fig_hm.update_layout(
            margin=dict(t=30, l=10, r=10, b=10),
            template="plotly_dark", height=280,
        )
        st.plotly_chart(fig_hm, use_container_width=True)
        st.divider()

    # --- In-flight position cards ---
    if open_pos:
        st.subheader(f"In-Flight Positions ({len(open_pos)} open)")
        for pos in open_pos:
            ticker   = pos["ticker"]
            pool_num = pos.get("pool", "?")
            entry    = float(pos.get("entry_price") or 0)
            current  = float(pos.get("current_price") or entry)
            target   = float(pos.get("target_price") or entry)
            stop     = float(pos.get("stop_loss") or 0)
            shares   = int(pos.get("shares") or 0)
            pnl      = float(pos.get("unrealized_pnl") or 0)
            high_wm  = float(pos.get("high_watermark") or entry)
            low_wm   = float(pos.get("low_watermark") or entry)

            icon       = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            pool_badge = (f"<span style='background:#2980b9;color:white;padding:2px 8px;"
                          f"border-radius:4px;font-size:12px'>P{pool_num}</span>")

            c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 3, 2])
            c1.markdown(f"**{icon} {ticker}** {pool_badge}", unsafe_allow_html=True)
            c2.markdown(f"Entry: **${entry:.2f}**")
            c3.markdown(f"Now: **${current:.2f}**")
            c4.markdown(f"Target: **${target:.2f}** | Stop: ${stop:.2f}")
            c5.markdown(
                f"<span style='color:{_pnl_color(pnl)};font-weight:bold'>{_fmt_pnl(pnl)}</span>",
                unsafe_allow_html=True,
            )

            # Progress bar: entry → current → target
            if target > entry:
                progress = max(0.0, min(1.0, (current - entry) / (target - entry)))
            else:
                progress = 0.0
            st.progress(
                progress,
                text=(f"Entry ${entry:.2f} → Target ${target:.2f}  |  "
                      f"High: ${high_wm:.2f}  Low: ${low_wm:.2f}  |  {shares} shares"),
            )

        st.divider()

    # --- Closed positions today ---
    if today_closed:
        st.subheader(f"Closed Today ({len(today_closed)} trades)")
        df_cl = pd.DataFrame(today_closed)
        show_cl = ["ticker", "pool", "entry_price", "fill_price", "close_price",
                   "realized_pnl", "close_reason", "mae", "mfe"]
        show_cl = [c for c in show_cl if c in df_cl.columns]
        df_cl_show = df_cl[show_cl].copy()

        def _color_pnl(val):
            if val is None: return ""
            return "color: #2ecc71" if val > 0 else "color: #e74c3c"

        if "realized_pnl" in df_cl_show.columns:
            st.dataframe(
                df_cl_show.style.map(_color_pnl, subset=["realized_pnl"]),
                use_container_width=True, hide_index=True,
            )
        else:
            st.dataframe(df_cl_show, use_container_width=True, hide_index=True)

        if "mae" in df_cl.columns and "mfe" in df_cl.columns:
            avg_mae = df_cl["mae"].mean()
            avg_mfe = df_cl["mfe"].mean()
            m1, m2 = st.columns(2)
            m1.metric("Avg MAE (adverse excursion)", f"${avg_mae:,.2f}" if avg_mae else "—",
                      help="How far against us positions moved. High MAE on losers = stops may be too tight.")
            m2.metric("Avg MFE (favorable excursion)", f"${avg_mfe:,.2f}" if avg_mfe else "—",
                      help="How far in our favour positions moved. High MFE on losers = targets may be too conservative.")
        st.divider()
    elif plan:
        st.info("No closed positions yet today")

    # --- Performance history table (always shown when data exists) ---
    st.subheader("Trading History")
    perf = db.select("b_daily_performance", order="date", limit=30)
    perf_total = [r for r in perf if r.get("pool") is None]
    if perf_total:
        df_perf = pd.DataFrame(perf_total).sort_values("date", ascending=False)
        _hist_rows = []
        for _, _pr in df_perf.iterrows():
            _wr = (_pr.get("win_rate") or 0) * 100
            _hist_rows.append({
                "Date":       str(_pr["date"])[:10],
                "P&L":        _fmt_pnl(_pr.get("gross_pnl", 0) or 0),
                "Trades":     int(_pr.get("trades_taken", 0) or 0),
                "Win %":      f"{_wr:.0f}%",
                "Expectancy": f"${_pr.get('expectancy', 0) or 0:,.2f}",
            })
        st.dataframe(pd.DataFrame(_hist_rows), use_container_width=True, hide_index=True)

        _n_days = len(df_perf)
        if _n_days < 5:
            st.info(f"📊 **{_n_days} trading day{'s' if _n_days != 1 else ''} recorded** — scorecard and trends become meaningful at 5+ days.")
        else:
            df_perf_sorted = df_perf.sort_values("date")
            df_perf_sorted["cumulative"] = df_perf_sorted["gross_pnl"].cumsum()
            fig = go.Figure()
            colors = ["#2ecc71" if v >= 0 else "#e74c3c" for v in df_perf_sorted["gross_pnl"]]
            fig.add_trace(go.Bar(x=df_perf_sorted["date"], y=df_perf_sorted["gross_pnl"],
                                 marker_color=colors, name="Daily P&L"))
            fig.add_trace(go.Scatter(x=df_perf_sorted["date"], y=df_perf_sorted["cumulative"],
                                     mode="lines+markers", name="Cumulative",
                                     line=dict(color="#3498db", width=2), yaxis="y2"))
            fig.update_layout(
                title="Daily P&L (bars) + Cumulative (line)",
                yaxis=dict(title="Daily P&L ($)"),
                yaxis2=dict(title="Cumulative ($)", overlaying="y", side="right"),
                template="plotly_dark", height=300,
            )
            st.plotly_chart(fig, use_container_width=True)

        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Total P&L",      _fmt_pnl(df_perf["gross_pnl"].sum()))
        s2.metric("Win Days",        f"{(df_perf['gross_pnl'] > 0).sum()}/{len(df_perf)}")
        s3.metric("Avg Win Rate",    f"{df_perf['win_rate'].mean()*100:.0f}%" if "win_rate" in df_perf.columns else "—")
        s4.metric("Avg Expectancy",  f"${df_perf['expectancy'].mean():,.2f}" if "expectancy" in df_perf.columns else "—")
    else:
        st.info("No performance history yet — runs after first EOD")

    if plan and plan.get("status") == "HALTED":
        st.divider()
        st.warning(f"**Halt reason:** {plan.get('risk_note', 'No details recorded')}")


# ============================================================
# PAGE 2: Performance
# ============================================================
elif page == "Performance":
    st.title("Performance History — Strategy B")

    # ── Date range selector ───────────────────────────────────────
    _all_perf_b   = db.select("b_daily_performance", order="date")
    _total_rows_b = [r for r in _all_perf_b if r.get("pool") is None]
    _total_days_b = len(_total_rows_b)
    _range_opts_b = {k: v for k, v in {"Last 7 days": 7, "Last 30 days": 30, "All time": None}.items()
                     if v is None or _total_days_b >= v}
    if not _range_opts_b:
        _range_opts_b = {"Last 7 days": 7}
    _selected_b = st.radio("Date range", list(_range_opts_b.keys()), horizontal=True, index=0)
    _n_days_b   = _range_opts_b[_selected_b]

    if not _total_rows_b:
        st.info("No performance data yet — runs after first EOD.")
        st.stop()

    # ── Filter to window ──────────────────────────────────────────
    df_b = pd.DataFrame(_total_rows_b).sort_values("date")
    if _n_days_b:
        _cutoff_b = (pd.Timestamp.today() - pd.Timedelta(days=_n_days_b)).strftime("%Y-%m-%d")
        df_b = df_b[df_b["date"] >= _cutoff_b]
    if df_b.empty:
        st.info(f"No data in the selected range ({_selected_b}).")
        st.stop()

    # ── Load positions for scorecard ──────────────────────────────
    _all_pos_b = db.select("b_positions")

    # ── Daily history table ───────────────────────────────────────
    _hist_b = []
    for _, _dr in df_b.sort_values("date", ascending=False).iterrows():
        _wr   = (_dr.get("win_rate") or 0) * 100
        _gpnl = _dr.get("gross_pnl", 0) or 0
        _hist_b.append({
            "Date":    str(_dr["date"])[:10],
            "P&L":     _fmt_pnl(_gpnl),
            "Trades":  int(_dr.get("trades_taken", 0) or 0),
            "Win %":   f"{_wr:.0f}%",
            "Regime":  _dr.get("regime_label") or "—",
            "VIX":     _dr.get("vix_level") or "—",
        })
    if _hist_b:
        st.dataframe(pd.DataFrame(_hist_b), use_container_width=True, hide_index=True)

    # ── P&L charts ────────────────────────────────────────────────
    _days_b = len(df_b)
    if _days_b >= 2:
        df_b_sorted = df_b.sort_values("date")
        df_b_sorted["cumulative"] = df_b_sorted["gross_pnl"].cumsum()
        _bar_colors = ["#2ecc71" if v >= 0 else "#e74c3c" for v in df_b_sorted["gross_pnl"]]
        fig_b = go.Figure()
        fig_b.add_trace(go.Bar(x=df_b_sorted["date"], y=df_b_sorted["gross_pnl"],
                               marker_color=_bar_colors, name="Daily P&L"))
        fig_b.add_trace(go.Scatter(x=df_b_sorted["date"], y=df_b_sorted["cumulative"],
                                   mode="lines+markers", name="Cumulative",
                                   line=dict(color="#3498db", width=2), yaxis="y2"))
        fig_b.update_layout(
            title="Daily P&L (bars) + Cumulative (line)",
            yaxis=dict(title="Daily P&L ($)"),
            yaxis2=dict(title="Cumulative ($)", overlaying="y", side="right"),
            template="plotly_dark", height=320,
        )
        st.plotly_chart(fig_b, use_container_width=True)

    st.markdown("---")

    # ── Scorecard ─────────────────────────────────────────────────
    ev_b  = _compute_metrics_b(perf_rows=df_b.to_dict("records"), positions=_all_pos_b) or {}
    days_b = len(df_b)

    if not ev_b or days_b < 5:
        st.info(f"📊 **Scorecard needs ≥ 5 trading days** — {days_b} day{'s' if days_b != 1 else ''} recorded so far.")
    else:
        _grade_b      = ev_b.get("grade", "?")
        _grade_label_b = {"A": "Excellent", "B": "Good", "C": "Mediocre", "D": "Poor"}.get(_grade_b, "")
        with st.expander(
            f"Agent Scorecard — {days_b} trading day{'s' if days_b != 1 else ''} of data ({_selected_b}) · "
            f"Grade **{_grade_b}** ({_grade_label_b})",
            expanded=True,
        ):
            # ── Verdict ──────────────────────────────────────────
            st.markdown("#### Verdict")
            st.caption(
                f"Score = P&L vs target (up to 40 pts: avg daily P&L ÷ ${DAILY_PROFIT_TARGET:,} × 40)  "
                f"+ Win day rate (30 pts: profitable days ÷ total days × 30)  "
                f"+ Trade win rate (30 pts: % of trades won × 30).  "
                f"Grade: A ≥ 80 · B ≥ 60 · C ≥ 40 · D < 40."
            )

            _pnl_b      = ev_b.get("avg_daily_pnl", 0)
            _pnl_pct_b  = _pnl_b / DAILY_PROFIT_TARGET * 100 if DAILY_PROFIT_TARGET else 0
            _win_days_b = ev_b.get("win_days", 0)
            _wd_pct_b   = _win_days_b / days_b * 100 if days_b else 0
            _wr_b       = ev_b.get("avg_win_rate", 0)
            _rr_b       = ev_b.get("actual_rr", 0)
            _cr_b       = ev_b.get("close_reasons", {})
            _total_cr_b = sum(_cr_b.values()) or 1

            _wins_b, _watchs_b, _actions_b = [], [], []

            if _pnl_b >= DAILY_PROFIT_TARGET:
                _wins_b.append(f"Avg daily P&L ${_pnl_b:,.0f} — on or above ${DAILY_PROFIT_TARGET:,} target")
            elif _pnl_pct_b >= 60:
                _watchs_b.append(f"Avg daily P&L ${_pnl_b:,.0f} is {_pnl_pct_b:.0f}% of ${DAILY_PROFIT_TARGET:,} target")
            else:
                _actions_b.append(f"Avg daily P&L ${_pnl_b:,.0f} well below ${DAILY_PROFIT_TARGET:,} target ({_pnl_pct_b:.0f}%)")

            if _wd_pct_b >= 80:
                _wins_b.append(f"{_win_days_b}/{days_b} profitable days ({_wd_pct_b:.0f}%) — consistent execution")
            elif _wd_pct_b >= 60:
                _watchs_b.append(f"{_win_days_b}/{days_b} profitable days ({_wd_pct_b:.0f}%) — more losing days than ideal")
            else:
                _actions_b.append(f"Only {_win_days_b}/{days_b} profitable days — strategy inconsistency")

            if _wr_b >= 60:
                _wins_b.append(f"{_wr_b:.0f}% trade win rate — well above 25% break-even for 3:1 R:R")
            elif _wr_b >= 50:
                _watchs_b.append(f"{_wr_b:.0f}% trade win rate — above break-even but room to improve")
            else:
                _watchs_b.append(f"{_wr_b:.0f}% trade win rate — approaching break-even; tighten entry criteria")

            if _rr_b >= 3.0:
                _wins_b.append(f"Reward:risk {_rr_b:.1f}x — meeting 3:1 target")
            elif _rr_b >= 2.0:
                _watchs_b.append(f"Reward:risk {_rr_b:.1f}x — below 3.0x target; losers running slightly large")
            else:
                _actions_b.append(f"Reward:risk {_rr_b:.1f}x — well below target; review stops and targets")

            _tgt_pct_b = _cr_b.get("TARGET", 0) / _total_cr_b * 100
            if _tgt_pct_b >= 50:
                _wins_b.append(f"{_tgt_pct_b:.0f}% of exits hit target — momentum strategy executing as designed")
            elif _cr_b.get("STOP", 0) / _total_cr_b > 0.5:
                _watchs_b.append(f"More stops than targets — entries may be too late in the move")

            _cs_b = ev_b.get("confidence_stats", {})
            _high_b, _low_b = _cs_b.get("HIGH"), _cs_b.get("LOW")
            if _high_b and _low_b:
                if _high_b["avg_pnl"] > _low_b["avg_pnl"]:
                    _wins_b.append(f"HIGH confidence trades earning ${_high_b['avg_pnl']:,.0f} avg vs ${_low_b['avg_pnl']:,.0f} for LOW — sizing justified")
                else:
                    _watchs_b.append(f"LOW outperforming HIGH (${_low_b['avg_pnl']:,.0f} vs ${_high_b['avg_pnl']:,.0f}) — confidence signal unreliable")

            _orphaned_b = ev_b.get("orphaned", [])
            if _orphaned_b:
                _actions_b.append(f"{len(_orphaned_b)} orphaned position(s) stuck OPEN from a prior day")
            if ev_b.get("rr_violations"):
                _actions_b.append(f"{len(ev_b['rr_violations'])} trade(s) submitted below {MIN_REWARD_RISK}x R:R — Claude constraint drift")
            if ev_b.get("duplicate_count", 0) > 0:
                _actions_b.append(f"{ev_b['duplicate_count']} duplicate ticker(s) same day — guardrail may have failed")
            _attempted_b = ev_b.get("total_attempted", 1) or 1
            _unfill_pct_b = ev_b.get("unfilled_count", 0) / _attempted_b * 100
            if _unfill_pct_b >= 15:
                _actions_b.append(f"{_unfill_pct_b:.0f}% unfilled rate — limit entry price too tight")
            elif _unfill_pct_b >= 5:
                _watchs_b.append(f"{_unfill_pct_b:.0f}% unfilled rate — monitor; rising trend is a problem")

            _grade_color_b = {"A": "#1e8449", "B": "#1e8449", "C": "#f39c12", "D": "#e74c3c"}.get(_grade_b, "#888")
            _grade_word_b  = {"A": "excellent", "B": "good", "C": "mixed", "D": "poor"}.get(_grade_b, "")
            _summary_parts_b = [" ".join(_wins_b)] if _wins_b else []
            _verdict_b = (
                f"<span style='color:{_grade_color_b}'><b>Grade {_grade_b} — {_grade_word_b.upper()}.</b></span> "
                + (" ".join(_summary_parts_b) if _summary_parts_b else "No standout positives yet — more data needed.")
            )
            _watch_text_b  = (f"<span style='color:#f39c12'><b>Watch:</b> {'  ·  '.join(_watchs_b)}.</span>"
                              if _watchs_b else "")
            _action_text_b = (f"<span style='color:#e74c3c'><b>Action required:</b> {'  ·  '.join(_actions_b)}.</span>"
                              if _actions_b else "<span style='color:#1e8449'>No action required.</span>")
            st.markdown(
                _verdict_b + ("  " + _watch_text_b if _watch_text_b else "") + "  " + _action_text_b,
                unsafe_allow_html=True,
            )

            st.markdown("---")

            # ── Key metrics ───────────────────────────────────────────
            st.markdown("#### Key Metrics")
            sc1_b, sc2_b, sc3_b, sc4_b, sc5_b = st.columns(5)
            _total_pnl_b = df_b["gross_pnl"].sum()
            sc1_b.metric("Total P&L",       _fmt_pnl(_total_pnl_b),
                         help=f"Cumulative realized P&L over the {days_b}-day window.")
            sc2_b.metric("Avg Daily P&L",   f"${_pnl_b:,.0f}",
                         delta=f"target ${DAILY_PROFIT_TARGET:,}",
                         help=f"Average realized P&L per trading day. Target: ${DAILY_PROFIT_TARGET:,}/day.")
            sc3_b.metric("Win Days",        f"{_win_days_b} / {days_b}",
                         help="Days where total realized P&L was positive. Target: ≥80%.")
            sc4_b.metric("Trade Win Rate",  f"{_wr_b:.1f}%",
                         help="% of individual trades that closed in profit. Break-even at 3:1 R:R = 25%.")
            sc5_b.metric("Actual R:R",      f"{_rr_b:.2f}x",
                         help="Avg winning trade ÷ avg losing trade. Target: ≥3.0x.")

            st.markdown("---")

            # ── Exit reasons + best/worst ──────────────────────────────
            _exit_explain_b = {
                "TARGET":   "Hit profit goal — ideal exit",
                "STOP":     "Stop-loss fired — cut loss",
                "EOD":      "Market closed, position still open — sold at whatever price",
                "LOCK_IN":  "Daily profit target hit — all positions closed to protect the day",
                "CLEANUP":  "Stale open position closed during reconciliation",
                "UNFILLED": "Limit order never filled — entry price was missed",
            }
            st.markdown("**Exit reasons**")
            _cr_rows_b = [
                {"Exit": k, "Count": v, "%": f"{v/_total_cr_b*100:.0f}%",
                 "What it means": _exit_explain_b.get(k, "—")}
                for k, v in sorted(_cr_b.items(), key=lambda x: -x[1])
            ]
            if _cr_rows_b:
                st.dataframe(pd.DataFrame(_cr_rows_b), use_container_width=True, hide_index=True)
            if ev_b.get("best_ticker"):
                st.success(f"Best: {ev_b['best_ticker']}  +${ev_b['best_pnl']:,.2f}")
            if ev_b.get("worst_ticker"):
                _worst_pnl_b = ev_b["worst_pnl"]
                if _worst_pnl_b >= 0:
                    st.success(f"Worst: {ev_b['worst_ticker']}  +${_worst_pnl_b:,.2f}  (all trades profitable)")
                else:
                    st.error(f"Worst: {ev_b['worst_ticker']}  ${_worst_pnl_b:,.2f}")

            st.markdown("---")

            # ── Integrity + Claude quality ─────────────────────────────
            st.markdown("#### Integrity & Claude Quality")
            int_col_b, qual_col_b = st.columns(2)

            with int_col_b:
                st.markdown("**Integrity checks**  — *guardrail audit; these should all be ✅*")
                _unfill_n_b = ev_b.get("unfilled_count", 0)
                _uf_icon_b  = "✅" if _unfill_pct_b < 10 else "⚠️"
                st.markdown(
                    f"{_uf_icon_b} UNFILLED rate: **{_unfill_n_b}** ({_unfill_pct_b:.0f}%)  "
                    f"<span style='color:#888;font-size:0.82em'>— limit order submitted but entry never filled. >10% means entry buffer is too tight.</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"{'✅' if not _orphaned_b else '❌'} Orphaned open positions: **{len(_orphaned_b)}**  "
                    f"<span style='color:#888;font-size:0.82em'>— positions from a prior day still showing OPEN. Should be zero.</span>",
                    unsafe_allow_html=True,
                )
                _dups_b = ev_b.get("duplicate_count", 0)
                st.markdown(
                    f"{'✅' if _dups_b == 0 else '❌'} Duplicate tickers same day: **{_dups_b}**  "
                    f"<span style='color:#888;font-size:0.82em'>— same ticker entered twice in one day. Guardrail should block this.</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"📉 Loss-limit days: **{ev_b.get('loss_limit_days', 0)}** / {days_b}  "
                    f"<span style='color:#888;font-size:0.82em'>— days where realized P&L went below the daily loss floor (${DAILY_LOSS_LIMIT:,}).</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"🎯 Lock-in days: **{ev_b.get('lock_in_days', 0)}** / {days_b}  "
                    f"<span style='color:#888;font-size:0.82em'>— days where realized P&L crossed the ${DAILY_LOCK_IN_TARGET:,} floor.</span>",
                    unsafe_allow_html=True,
                )
                _gap_rows_b = ev_b.get("gap_rows", [])
                if _gap_rows_b:
                    _latest_gap_b = float(_gap_rows_b[-1]["friction_gap"])
                    _avg_gap_b    = sum(float(r["friction_gap"]) for r in _gap_rows_b) / len(_gap_rows_b)
                    _gap_sign_b   = "+" if _latest_gap_b >= 0 else ""
                    _gap_icon_b   = "✅" if abs(_latest_gap_b) < 50 else ("⚠️" if abs(_latest_gap_b) < 200 else "❌")
                    st.markdown(
                        f"{_gap_icon_b} Broker friction gap (latest): **{_gap_sign_b}${_latest_gap_b:,.2f}**  "
                        f"<span style='color:#888;font-size:0.82em'>— Strategy B Alpaca fills minus our P&L calc. "
                        f"Avg: {'+' if _avg_gap_b >= 0 else ''}${_avg_gap_b:,.2f}. "
                        f"<$50 = ✅ · $50–$200 = ⚠️ · >$200 = ❌ investigate.</span>",
                        unsafe_allow_html=True,
                    )

            with qual_col_b:
                st.markdown("**Claude quality checks**  — *validates Claude is following strategy rules*")
                _rr_v_b = ev_b.get("rr_violations", [])
                st.markdown(
                    f"{'✅' if not _rr_v_b else '❌'} R:R violations: **{len(_rr_v_b)}** trades below {MIN_REWARD_RISK}x  "
                    f"<span style='color:#888;font-size:0.82em'>— R:R = (target − entry) ÷ (entry − stop). "
                    f"Claude must submit trades where gain ≥ {MIN_REWARD_RISK}× potential loss.</span>",
                    unsafe_allow_html=True,
                )
                if _rr_v_b:
                    for _v_b in _rr_v_b:
                        st.caption(f"  → {_v_b['ticker']} R:R {_v_b['rr']:.2f}x")
                _sz_v_b = ev_b.get("size_violations", [])
                st.markdown(
                    f"{'✅' if not _sz_v_b else '⚠️'} Position size violations: **{len(_sz_v_b)}**  "
                    f"<span style='color:#888;font-size:0.82em'>— Each position must be ${MIN_POSITION_PCT*TOTAL_CAPITAL:,.0f}–${MAX_POSITION_PCT*TOTAL_CAPITAL:,.0f} "
                    f"({MIN_POSITION_PCT*100:.0f}%–{MAX_POSITION_PCT*100:.0f}% of ${TOTAL_CAPITAL:,} capital).</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    "**Confidence cohort**  — *does Claude's confidence signal predict better outcomes?*  "
                    f"<span style='color:#888;font-size:0.82em'>HIGH → $7K, MEDIUM → $6K, LOW → $5K. "
                    "If HIGH confidence doesn't outperform LOW, the signal is unreliable.</span>",
                    unsafe_allow_html=True,
                )
                _conf_rows_b = []
                for _level_b in ("HIGH", "MEDIUM", "LOW"):
                    _s_b = _cs_b.get(_level_b)
                    if _s_b:
                        _conf_rows_b.append({
                            "Level": _level_b, "Trades": _s_b["count"],
                            "Win %": f"{_s_b['win_rate']:.1f}%",
                            "Avg P&L": f"${_s_b['avg_pnl']:,.2f}",
                            "Total": f"${_s_b['total_pnl']:,.0f}",
                        })
                if _conf_rows_b:
                    st.dataframe(pd.DataFrame(_conf_rows_b), use_container_width=True, hide_index=True)
                if _high_b and _low_b:
                    _delta_b = _high_b["avg_pnl"] - _low_b["avg_pnl"]
                    if _delta_b > 0:
                        st.success(f"HIGH outperforming LOW by ${_delta_b:,.2f} avg — sizing justified")
                    else:
                        st.warning(f"LOW outperforming HIGH by ${abs(_delta_b):,.2f} — confidence signal unreliable")

    # ── Pool breakdown ────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Pool P&L Breakdown")
    _pool_rows = [r for r in _all_perf_b if r.get("pool") is not None]
    if _pool_rows:
        _pool_df = pd.DataFrame(_pool_rows)
        if _n_days_b:
            _pool_df = _pool_df[_pool_df["date"] >= _cutoff_b]
        if not _pool_df.empty:
            _pool_summary = (
                _pool_df.groupby("pool")["gross_pnl"]
                .sum()
                .reset_index()
                .rename(columns={"pool": "Pool", "gross_pnl": "Total P&L"})
            )
            _pool_summary["Pool"] = _pool_summary["Pool"].apply(lambda p: f"Pool {p}")
            _bar_col = ["#2ecc71" if v >= 0 else "#e74c3c" for v in _pool_summary["Total P&L"]]
            _fig_pool = go.Figure(go.Bar(
                x=_pool_summary["Pool"], y=_pool_summary["Total P&L"],
                marker_color=_bar_col, text=_pool_summary["Total P&L"].apply(lambda v: _fmt_pnl(v)),
                textposition="auto",
            ))
            _fig_pool.update_layout(
                title=f"P&L by Pool — {_selected_b}",
                yaxis_title="Total P&L ($)", template="plotly_dark", height=280,
            )
            st.plotly_chart(_fig_pool, use_container_width=True)
    else:
        st.caption("No pool-level breakdown recorded yet.")


# ============================================================
# PAGE 1: Strategy B
# ============================================================
elif page == "Strategy B":
    st.title("Trading Agent B — Blue Chip Pool Strategy")
    st.markdown(f"Today: {date.today()} | Universe: {len(POOL_2_SEED)} blue chip seed stocks")

    # --- Today's Pool 3 ---
    st.subheader("Today's Pool 3 — Daily Elite Picks")
    plans = db.select("b_trade_plans", filters={"date": str(date.today())}, limit=1)
    if plans:
        pool3 = plans[0].get("pool3_tickers") or []
        if pool3:
            cols = st.columns(min(len(pool3), 5))
            for i, t in enumerate(pool3):
                cols[i % 5].metric(t, SECTOR_MAP.get(t, "—"))
        else:
            st.info("No Pool 3 selected today yet")
    else:
        st.info("No trade plan for today yet")

    st.divider()

    # --- Pool 2 Scoreboard ---
    st.subheader("Pool 2 — Behavioral Shortlist")
    pool2_rows = db.select("b_pools", filters={"pool": 2})
    if pool2_rows:
        df2 = pd.DataFrame(pool2_rows)[["ticker", "rolling_score", "trade_count", "win_count", "added_at"]]
        df2["win_rate"] = df2.apply(
            lambda r: round(r["win_count"] / r["trade_count"], 2) if r["trade_count"] > 0 else None, axis=1
        )
        df2 = df2.sort_values("rolling_score", ascending=False)
        df2.columns = ["Ticker", "Rolling Score (7d)", "Trades", "Wins", "Added", "Win Rate"]
        st.dataframe(df2, use_container_width=True, hide_index=True)
    else:
        st.info("Pool 2 not seeded yet — run premarket first")

    st.divider()

    # --- Open Positions ---
    st.subheader("Open Positions")
    open_pos = db.select("b_positions", filters={"status": "OPEN"})
    if open_pos:
        df_open = pd.DataFrame(open_pos)[[
            "ticker", "pool", "entry_price", "target_price", "stop_loss",
            "shares", "position_size", "unrealized_pnl"
        ]]
        df_open.columns = ["Ticker", "Pool", "Entry", "Target", "Stop", "Shares", "Size", "Unrealized P&L"]
        total_unreal = df_open["Unrealized P&L"].sum()
        st.dataframe(df_open, use_container_width=True, hide_index=True)
        st.metric("Total Unrealized P&L", f"${total_unreal:,.2f}",
                  delta_color="normal" if total_unreal >= 0 else "inverse")
    else:
        st.info("No open positions")

    st.divider()

    # --- Daily P&L by Pool ---
    st.subheader("Daily P&L by Pool")
    perf = db.select("b_daily_performance")
    if perf:
        df_perf = pd.DataFrame(perf)
        df_perf["date"] = pd.to_datetime(df_perf["date"])
        df_perf_total = df_perf[df_perf["pool"].isna()].copy()

        col1, col2, col3 = st.columns(3)
        if not df_perf_total.empty:
            latest = df_perf_total.sort_values("date").iloc[-1]
            col1.metric("Today Gross P&L", f"${latest.get('gross_pnl', 0):,.2f}")
            col2.metric("Win Rate", f"{latest.get('win_rate', 0)*100:.0f}%")
            col3.metric("Expectancy / Trade", f"${latest.get('expectancy', 0):,.2f}")

        df_pool = df_perf[df_perf["pool"].notna()].copy()
        if not df_pool.empty:
            fig = px.bar(
                df_pool, x="date", y="gross_pnl", color="pool",
                barmode="group", title="Gross P&L by Pool",
                labels={"gross_pnl": "P&L ($)", "pool": "Pool"},
            )
            st.plotly_chart(fig, use_container_width=True)

        if "regime_label" in df_perf_total.columns:
            st.subheader("Regime Log — Passive Observation")
            st.markdown("No trades are blocked by regime yet. This data will tell us whether to add a gate after 30 days.")
            regime_cols = ["date", "regime_label", "vix_level", "fear_greed",
                           "spy_change_pct", "gross_pnl", "win_rate", "trades_taken"]
            available = [c for c in regime_cols if c in df_perf_total.columns]
            df_regime = df_perf_total[available].sort_values("date", ascending=False)
            df_regime.columns = [c.replace("_", " ").title() for c in df_regime.columns]
            st.dataframe(df_regime, use_container_width=True, hide_index=True)

            if not df_perf_total.empty:
                regime_summary = df_perf_total.groupby("regime_label").agg(
                    days=("date", "count"),
                    total_pnl=("gross_pnl", "sum"),
                    avg_pnl=("gross_pnl", "mean"),
                    avg_win_rate=("win_rate", "mean"),
                ).reset_index()
                regime_summary.columns = ["Regime", "Days", "Total P&L", "Avg P&L/Day", "Avg Win Rate"]
                st.dataframe(regime_summary, use_container_width=True, hide_index=True)
    else:
        st.info("No performance data yet")

    st.divider()

    # --- Recent Stock Scores ---
    st.subheader("Recent Stock Scores (last 7 days)")
    scores = db.select("b_stock_scores")
    if scores:
        df_sc = pd.DataFrame(scores)
        df_sc = df_sc[df_sc["traded"] == True] if "traded" in df_sc.columns else df_sc
        df_sc["date"] = pd.to_datetime(df_sc["date"])
        df_sc = df_sc.sort_values("date", ascending=False).head(50)
        show = ["date", "ticker", "pool", "win", "pnl", "daily_score", "rolling_7d"]
        st.dataframe(df_sc[show], use_container_width=True, hide_index=True)
    else:
        st.info("No scores yet — runs after first EOD")
