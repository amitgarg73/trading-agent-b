"""
Strategy B Dashboard — Streamlit app.

Page 1: Strategy B — pools, scores, P&L, positions
Page 2: A vs B Comparison — side-by-side expectancy, win rate, P&L
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import date, timedelta
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db
from config.settings import DASHBOARD_PASSWORD
from config.blue_chips import POOL_2_SEED, SECTOR_MAP

st.set_page_config(page_title="Trading Agent B", page_icon="📊", layout="wide")

# --- Auth ---
def _check_password() -> bool:
    if st.session_state.get("authenticated"):
        return True
    pwd = st.sidebar.text_input("Password", type="password")
    if pwd == DASHBOARD_PASSWORD:
        st.session_state["authenticated"] = True
        return True
    if pwd:
        st.sidebar.error("Wrong password")
    return False

if not _check_password():
    st.stop()

# --- Navigation ---
page = st.sidebar.radio("View", ["Today", "Strategy B", "A vs B Comparison"])


def _fmt_pnl(v: float) -> str:
    return f"+${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"

# ============================================================
# PAGE 0: Today — Daily Summary
# ============================================================
if page == "Today":
    today_str = str(date.today())

    # Fetch today's plan
    plans = db.select("b_trade_plans", filters={"date": today_str}, limit=1)
    plan  = plans[0] if plans else None

    # Status badge
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

    # --- KPI row ---
    open_pos    = db.select("b_positions", filters={"status": "OPEN"})
    all_closed  = db.select("b_positions", filters={"status": "CLOSED"})
    today_closed = [
        p for p in all_closed
        if str(p.get("closed_at", ""))[:10] == today_str
        and p.get("close_reason") not in ("CLEANUP",)
    ]

    realized   = sum(p.get("realized_pnl", 0) or 0 for p in today_closed)
    unrealized = sum(p.get("unrealized_pnl", 0) or 0 for p in open_pos)
    total_pnl  = realized + unrealized
    wins       = [p for p in today_closed if (p.get("realized_pnl") or 0) > 0]
    win_rate   = len(wins) / len(today_closed) * 100 if today_closed else 0
    pool3      = plan.get("pool3_tickers") or [] if plan else []

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Today P&L",    _fmt_pnl(total_pnl),
              delta=f"{total_pnl/50_000*100:+.2f}% of capital",
              delta_color="normal" if total_pnl >= 0 else "inverse")
    k2.metric("Realized",     _fmt_pnl(realized))
    k3.metric("Unrealized",   _fmt_pnl(unrealized))
    k4.metric("Win Rate",     f"{win_rate:.0f}%" if today_closed else "—",
              delta=f"{len(wins)}W / {len(today_closed)-len(wins)}L" if today_closed else None,
              delta_color="off")
    k5.metric("Open Positions", len(open_pos))
    k6.metric("Pool 3 Picks",   len(pool3))

    st.divider()

    # --- Pool 3 tickers ---
    if pool3:
        st.subheader("Today's Pool 3")
        cols = st.columns(min(len(pool3), 5))
        for i, t in enumerate(pool3):
            cols[i % 5].metric(t, SECTOR_MAP.get(t, "—"))
        st.divider()

    # --- Open positions ---
    if open_pos:
        st.subheader("Open Positions")
        df_open = pd.DataFrame(open_pos)
        show_open = ["ticker", "pool", "entry_price", "fill_price", "target_price",
                     "stop_loss", "shares", "unrealized_pnl", "high_watermark", "low_watermark"]
        show_open = [c for c in show_open if c in df_open.columns]
        st.dataframe(df_open[show_open], use_container_width=True, hide_index=True)
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

        # MAE/MFE insight
        if "mae" in df_cl.columns and "mfe" in df_cl.columns:
            avg_mae = df_cl["mae"].mean()
            avg_mfe = df_cl["mfe"].mean()
            m1, m2 = st.columns(2)
            m1.metric("Avg MAE (adverse excursion)", f"${avg_mae:,.2f}" if avg_mae else "—",
                      help="How far against us positions moved before closing. High MAE on losers = stops may be too tight.")
            m2.metric("Avg MFE (favorable excursion)", f"${avg_mfe:,.2f}" if avg_mfe else "—",
                      help="How far in our favour positions moved. High MFE on losers = targets may be too conservative.")
        st.divider()
    elif plan:
        st.info("No closed positions yet today")

    # --- 7-day P&L sparkline ---
    st.subheader("Last 7 Trading Days")
    perf = db.select("b_daily_performance", order="date", limit=7)
    perf_total = [r for r in perf if r.get("pool") is None]
    if perf_total:
        df_perf = pd.DataFrame(perf_total)
        df_perf["date"] = pd.to_datetime(df_perf["date"])
        df_perf = df_perf.sort_values("date")
        df_perf["cumulative"] = df_perf["gross_pnl"].cumsum()

        import plotly.graph_objects as go
        fig = go.Figure()
        colors = ["#2ecc71" if v >= 0 else "#e74c3c" for v in df_perf["gross_pnl"]]
        fig.add_trace(go.Bar(
            x=df_perf["date"], y=df_perf["gross_pnl"],
            marker_color=colors, name="Daily P&L",
        ))
        fig.add_trace(go.Scatter(
            x=df_perf["date"], y=df_perf["cumulative"],
            mode="lines+markers", name="Cumulative",
            line=dict(color="#3498db", width=2), yaxis="y2",
        ))
        fig.update_layout(
            title="Daily P&L (bars) + Cumulative (line)",
            yaxis=dict(title="Daily P&L ($)"),
            yaxis2=dict(title="Cumulative ($)", overlaying="y", side="right"),
            template="plotly_dark", height=350,
        )
        st.plotly_chart(fig, use_container_width=True)

        # Summary stats
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("7-day P&L",   _fmt_pnl(df_perf["gross_pnl"].sum()))
        s2.metric("Win Days",    f"{(df_perf['gross_pnl'] > 0).sum()}/{len(df_perf)}")
        s3.metric("Avg Win Rate", f"{df_perf['win_rate'].mean()*100:.0f}%" if "win_rate" in df_perf else "—")
        s4.metric("Avg Expectancy", f"${df_perf['expectancy'].mean():,.2f}" if "expectancy" in df_perf else "—")
    else:
        st.info("No performance history yet — runs after first EOD")

    # --- Halt reason (if halted) ---
    if plan and plan.get("status") == "HALTED":
        st.divider()
        st.warning(f"**Halt reason:** {plan.get('risk_note', 'No details recorded')}")


# ============================================================
# PAGE 1: Strategy B
# ============================================================
if page == "Strategy B":
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

        # P&L by pool chart
        df_pool = df_perf[df_perf["pool"].notna()].copy()
        if not df_pool.empty:
            fig = px.bar(
                df_pool, x="date", y="gross_pnl", color="pool",
                barmode="group", title="Gross P&L by Pool",
                labels={"gross_pnl": "P&L ($)", "pool": "Pool"},
            )
            st.plotly_chart(fig, use_container_width=True)

        # Regime observation table
        if "regime_label" in df_perf_total.columns:
            st.subheader("Regime Log — Passive Observation")
            st.caption("No trades are blocked by regime yet. This data will tell us whether to add a gate after 30 days.")
            regime_cols = ["date", "regime_label", "vix_level", "fear_greed",
                           "spy_change_pct", "gross_pnl", "win_rate", "trades_taken"]
            available = [c for c in regime_cols if c in df_perf_total.columns]
            df_regime = df_perf_total[available].sort_values("date", ascending=False)
            df_regime.columns = [c.replace("_", " ").title() for c in df_regime.columns]

            # Color rows by regime
            def _color_regime(val):
                colors = {"FEAR": "background-color: #5c1a1a",
                          "HIGH_VOL": "background-color: #4a3800",
                          "TREND": "background-color: #1a3a1a",
                          "CHOPPY": "background-color: #1a1a3a"}
                return colors.get(val, "")

            st.dataframe(df_regime, use_container_width=True, hide_index=True)

            # P&L by regime summary
            if "regime_label" in df_perf_total.columns and not df_perf_total.empty:
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


# ============================================================
# PAGE 2: A vs B Comparison
# ============================================================
else:
    st.title("Strategy A vs Strategy B — Comparison")
    st.caption("Same capital, same Alpaca account, same time period — which selection approach wins?")

    days = st.sidebar.slider("Lookback (days)", 7, 60, 14)
    cutoff = str(date.today() - timedelta(days=days))

    # --- Fetch Strategy B performance ---
    b_perf = db.select("b_daily_performance")
    b_total = [r for r in b_perf if r.get("pool") is None and str(r.get("date","")) >= cutoff]

    # --- Fetch Strategy A performance (positions table, no b_ prefix) ---
    try:
        a_positions = db.select("positions", filters={"status": "CLOSED"})
        a_positions = [p for p in a_positions if str(p.get("closed_at",""))[:10] >= cutoff]
    except Exception:
        a_positions = []

    # Build Strategy A daily P&L
    a_by_date: dict[str, list] = {}
    for p in a_positions:
        d = str(p.get("closed_at",""))[:10]
        a_by_date.setdefault(d, []).append(p)

    a_daily = []
    for d, positions in sorted(a_by_date.items()):
        wins   = [p for p in positions if (p.get("realized_pnl") or 0) > 0]
        gross  = sum(p.get("realized_pnl") or 0 for p in positions)
        n      = len(positions)
        a_daily.append({
            "date":      d,
            "strategy":  "A",
            "gross_pnl": round(gross, 2),
            "trades":    n,
            "wins":      len(wins),
            "win_rate":  round(len(wins)/n, 2) if n else 0,
        })

    b_daily = [{
        "date":      str(r["date"]),
        "strategy":  "B",
        "gross_pnl": r.get("gross_pnl", 0),
        "trades":    r.get("trades_taken", 0),
        "wins":      r.get("wins", 0),
        "win_rate":  r.get("win_rate", 0),
    } for r in b_total]

    combined = pd.DataFrame(a_daily + b_daily)

    if combined.empty:
        st.info("No comparison data yet — both strategies need live trading data")
        st.stop()

    combined["date"] = pd.to_datetime(combined["date"])

    # --- Summary metrics ---
    st.subheader("Summary Metrics")
    col1, col2 = st.columns(2)

    for strat, col in [("A", col1), ("B", col2)]:
        df_s = combined[combined["strategy"] == strat]
        total_pnl  = df_s["gross_pnl"].sum()
        total_trades = df_s["trades"].sum()
        avg_win_rate = df_s["win_rate"].mean()
        avg_pnl  = total_pnl / total_trades if total_trades > 0 else 0
        col.markdown(f"### Strategy {strat}")
        col.metric("Total P&L", f"${total_pnl:,.2f}")
        col.metric("Total Trades", int(total_trades))
        col.metric("Avg Win Rate", f"{avg_win_rate*100:.0f}%")
        col.metric("Avg P&L / Trade", f"${avg_pnl:,.2f}")

    st.divider()

    # --- Cumulative P&L chart ---
    st.subheader("Cumulative P&L")
    fig = go.Figure()
    for strat, color in [("A", "#1f77b4"), ("B", "#ff7f0e")]:
        df_s = combined[combined["strategy"] == strat].sort_values("date")
        df_s["cumulative"] = df_s["gross_pnl"].cumsum()
        fig.add_trace(go.Scatter(
            x=df_s["date"], y=df_s["cumulative"],
            mode="lines+markers", name=f"Strategy {strat}",
            line=dict(color=color, width=2),
        ))
    fig.update_layout(
        title="Cumulative P&L: Strategy A vs B",
        xaxis_title="Date", yaxis_title="P&L ($)",
        template="plotly_dark",
    )
    st.plotly_chart(fig, use_container_width=True)

    # --- Win rate comparison ---
    st.subheader("Daily Win Rate")
    fig2 = px.line(
        combined, x="date", y="win_rate", color="strategy",
        title="Daily Win Rate: A vs B",
        labels={"win_rate": "Win Rate", "strategy": "Strategy"},
        color_discrete_map={"A": "#1f77b4", "B": "#ff7f0e"},
        template="plotly_dark",
    )
    st.plotly_chart(fig2, use_container_width=True)

    # --- Daily trades count ---
    st.subheader("Trades Per Day")
    fig3 = px.bar(
        combined, x="date", y="trades", color="strategy",
        barmode="group", title="Trades Per Day: A vs B",
        color_discrete_map={"A": "#1f77b4", "B": "#ff7f0e"},
        template="plotly_dark",
    )
    st.plotly_chart(fig3, use_container_width=True)

    # --- Strategy B pool breakdown ---
    st.subheader("Strategy B — P&L by Pool")
    b_pool = [r for r in b_perf if r.get("pool") is not None and str(r.get("date","")) >= cutoff]
    if b_pool:
        df_bp = pd.DataFrame(b_pool)
        df_bp["date"] = pd.to_datetime(df_bp["date"])
        fig4 = px.bar(
            df_bp, x="date", y="gross_pnl", color="pool",
            barmode="group", title="Strategy B P&L by Pool (1=Broad, 2=Blue Chips, 3=Elite)",
            template="plotly_dark",
        )
        st.plotly_chart(fig4, use_container_width=True)
    else:
        st.info("Pool breakdown data available after first EOD run")
