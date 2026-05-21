"""
Strategy B Dashboard — Streamlit app.

Page 0: Summary — KPIs, in-flight positions, trade plan, heatmap
Page 1: Today — intraday detail with market context, watermarks, 7-day sparkline
Page 2: Strategy B — pools, scores, P&L history
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
)
from config.blue_chips import POOL_2_SEED, SECTOR_MAP

st.set_page_config(page_title="Trading Agent B", page_icon="📊", layout="wide")

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
page = st.sidebar.radio("View", ["Summary", "Today", "Strategy B"])


def _fmt_pnl(v: float) -> str:
    return f"+${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"


def _pnl_color(v: float) -> str:
    return "#2ecc71" if v > 0 else "#e74c3c" if v < 0 else "#95a5a6"


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
    trades = db.select("b_planned_trades", filters={"plan_id": plan["id"]}) if plan else []

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
        st.caption("No open positions right now.")

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
        st.caption(f"Plan ready ({len(trades)} trades) — waiting for market open.")
    else:
        st.caption("No trade plan yet.")

    st.divider()

    # --- Trade heatmap ---
    st.subheader("🗺️ Trade Heatmap — P&L by Stock")
    heatmap_src = executed_trades or trades
    if heatmap_src:
        hm_labels, hm_pnl, hm_size, hm_text, hm_hover = [], [], [], [], []
        for t in heatmap_src:
            status_label, pnl_val = _sum_status(t["ticker"])
            ticker   = t["ticker"]
            pos_size = float(t.get("position_size") or 5000)
            hm_labels.append(ticker)
            hm_pnl.append(pnl_val)
            hm_size.append(pos_size)
            hm_text.append(f"{ticker}<br>{_fmt_pnl(pnl_val)}")
            hm_hover.append(
                f"<b>{ticker}</b> — {SECTOR_MAP.get(ticker, '')} | Pool {t.get('pool','?')}<br>"
                f"Status: {status_label}<br>"
                f"P&L: {_fmt_pnl(pnl_val)}<br>"
                f"Entry: ${float(t.get('entry_price') or 0):.2f} → Target: ${float(t.get('target_price') or 0):.2f}"
            )

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
            height=380,
            margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_hm, use_container_width=True)
        st.caption("Block size = position size. Color = P&L (green = profit, red = loss, gray = pending/flat).")
    else:
        st.info("No trades to display yet.")


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
        trades = db.select("b_planned_trades", filters={"plan_id": plan["id"]})

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

    # --- Pool 3 tickers ---
    if pool3:
        st.subheader("Today's Pool 3")
        cols = st.columns(min(len(pool3), 5))
        for i, t in enumerate(pool3):
            cols[i % 5].metric(t, SECTOR_MAP.get(t, "—"))
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
# PAGE 1: Strategy B
# ============================================================
elif page == "Strategy B":
    st.title("Trading Agent B — Blue Chip Pool Strategy")
    st.caption(f"Today: {date.today()} | Universe: {len(POOL_2_SEED)} blue chip seed stocks")

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
            st.caption("No trades are blocked by regime yet. This data will tell us whether to add a gate after 30 days.")
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
