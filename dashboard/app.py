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
page = st.sidebar.radio("View", ["Strategy B", "A vs B Comparison"])

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
