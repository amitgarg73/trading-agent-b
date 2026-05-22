"""Generates Trading Agent B full-day agent workflow diagram as PNG."""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch

# ── Canvas ─────────────────────────────────────────────────────────────────────
W, H = 28, 48
DPI  = 120
fig, ax = plt.subplots(figsize=(W, H))
ax.set_xlim(0, W)
ax.set_ylim(0, H)
ax.axis("off")
fig.patch.set_facecolor("#F4F6F8")
ax.set_facecolor("#F4F6F8")

# ── Colors ─────────────────────────────────────────────────────────────────────
PRE   = "#1A3A6A"   # navy   – premarket
INT   = "#155724"   # green  – intraday
EOD   = "#7B3100"   # brown  – eod
DB    = "#2D3748"   # slate  – infra / supabase
SKIP  = "#C0392B"   # red    – decision gate
WHITE = "#FFFFFF"
LIGHT = "#F0F4FF"

PRE_L = "#2E5FA3"
INT_L = "#1E7A3E"
EOD_L = "#C96A1A"
DB_L  = "#4A5568"

FONT = "DejaVu Sans"

# ── Helpers ────────────────────────────────────────────────────────────────────
BW, BH = 5.2, 1.0
GAP_X  = 0.8
ROW_H  = 2.0


def rbox(cx, cy, w, h, title, sub="", fill=PRE_L, title_sz=10, sub_sz=8.5):
    pad  = 0.12
    rect = FancyBboxPatch((cx - w/2, cy - h/2), w, h,
                          boxstyle=f"round,pad={pad},rounding_size=0.22",
                          linewidth=0, facecolor=fill, zorder=4)
    ax.add_patch(rect)
    rect2 = FancyBboxPatch((cx - w/2, cy - h/2), w, h,
                           boxstyle=f"round,pad={pad},rounding_size=0.22",
                           linewidth=1.1, edgecolor="white", facecolor="none",
                           alpha=0.22, zorder=5)
    ax.add_patch(rect2)
    if sub:
        ax.text(cx, cy + 0.19, title, fontsize=title_sz, fontweight="bold",
                color=WHITE, fontfamily=FONT, va="center", ha="center", zorder=6)
        ax.text(cx, cy - 0.24, sub, fontsize=sub_sz, color=WHITE,
                fontfamily=FONT, va="center", ha="center", zorder=6, alpha=0.85)
    else:
        ax.text(cx, cy, title, fontsize=title_sz, fontweight="bold",
                color=WHITE, fontfamily=FONT, va="center", ha="center", zorder=6)


def diamond(cx, cy, w, h, label, fill=SKIP):
    pts = [(cx, cy+h/2), (cx+w/2, cy), (cx, cy-h/2), (cx-w/2, cy)]
    poly = plt.Polygon(pts, closed=True, facecolor=fill, edgecolor="white",
                       linewidth=1, zorder=4, alpha=0.9)
    ax.add_patch(poly)
    ax.text(cx, cy, label, fontsize=9, fontweight="bold", color=WHITE,
            fontfamily=FONT, va="center", ha="center", zorder=6)


def section_bg(y_bot, y_top, fill, alpha=0.06):
    rect = FancyBboxPatch((0.5, y_bot), W - 1.0, y_top - y_bot,
                          boxstyle="round,pad=0,rounding_size=0.4",
                          linewidth=1.4, edgecolor=fill,
                          facecolor=fill, alpha=alpha, zorder=1)
    ax.add_patch(rect)


def section_hdr(y, label, fill, right_note=""):
    pill = FancyBboxPatch((0.8, y - 0.38), 9.0, 0.76,
                          boxstyle="round,pad=0,rounding_size=0.38",
                          linewidth=0, facecolor=fill, alpha=0.15, zorder=3)
    ax.add_patch(pill)
    ax.text(1.4, y, label, fontsize=12.5, fontweight="bold", color=fill,
            fontfamily=FONT, va="center", ha="left", zorder=4)
    if right_note:
        ax.text(W - 0.8, y, right_note, fontsize=8.5, color="#888888",
                fontfamily=FONT, va="center", ha="right", zorder=4, fontstyle="italic")


def arr(x1, y1, x2, y2, color="#888888", lw=1.7, label="", label_side="right"):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=lw,
                                mutation_scale=15), zorder=3)
    if label:
        mx, my = (x1+x2)/2, (y1+y2)/2
        dx = 0.18 if label_side == "right" else -0.18
        ha = "left" if label_side == "right" else "right"
        ax.text(mx + dx, my, label, fontsize=7.5, color=color,
                fontstyle="italic", fontfamily=FONT, va="center", ha=ha, zorder=4)


def v_arr(cx, y1, y2, color="#888888", lw=1.7, label=""):
    arr(cx, y1, cx, y2, color=color, lw=lw, label=label)


def h_arr(x1, x2, cy, color="#888888", lw=1.7, label=""):
    arr(x1, cy, x2, cy, color=color, lw=lw, label=label)


def elbow(x1, y1, x2, y2, color="#888888", lw=1.7):
    ax.plot([x1, x1, x2], [y1, y2, y2], color=color, lw=lw, zorder=3,
            solid_capstyle="round")
    ax.annotate("", xy=(x2, y2), xytext=(x2 - 0.01 * (1 if x2 > x1 else -1), y2),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=lw,
                                mutation_scale=15), zorder=3)


# ══════════════════════════════════════════════════════════════════════════════
# TITLE
# ══════════════════════════════════════════════════════════════════════════════
ax.text(W/2, 47.15, "Trading Agent B — Full Day Workflow",
        fontsize=20, fontweight="bold", color="#1A3A6A",
        fontfamily=FONT, va="center", ha="center", zorder=6)
ax.text(W/2, 46.45, "Blue Chip Pool Strategy  ·  Premarket  ·  Intraday Momentum  ·  EOD Pool Scoring",
        fontsize=11, color="#666666",
        fontfamily=FONT, va="center", ha="center", zorder=6)
ax.plot([1.2, W-1.2], [46.0, 46.0], color="#CCCCCC", lw=1.1, zorder=3)


# ══════════════════════════════════════════════════════════════════════════════
# ① PREMARKET   y: 30.5 → 45.5
# ══════════════════════════════════════════════════════════════════════════════
section_bg(30.5, 45.5, PRE)
section_hdr(45.0, "①  PREMARKET  —  10:00 AM ET", PRE,
            right_note="orchestrator.py → premarket()")

CX = W / 2   # center x = 14

# Row A: Pool Manager ─────────────────────────────────────────────────────────
rbox(CX, 44.0, BW, BH, "Pool Manager", "Verify b_pools membership · Seed if empty", PRE_L)

# Row B: Pool Filter ──────────────────────────────────────────────────────────
rbox(CX, 42.2, BW, BH, "Pool Filter", "Pool 2 → Pool 3 · Score 5 signals · Top 8-10", PRE_L)
v_arr(CX, 43.5, 42.7, PRE_L, lw=2, label="  Pool 1+2 membership")

# Row C: Scanner + Market Context (split) ─────────────────────────────────────
rbox(CX - 3.2, 40.4, 5.0, BH, "Scanner", "Behavioral score · VWAP · ATR · Volume · RSI", PRE_L)
rbox(CX + 3.2, 40.4, 5.0, BH, "Market Context", "VIX gate · F&G · Futures · Calendar", PRE_L)
v_arr(CX, 41.7, 40.9, PRE_L, lw=2, label="  Pool 3 candidates")
# elbow out to both
elbow(CX, 40.9, CX - 3.2, 40.9, PRE_L, lw=1.8)
elbow(CX, 40.9, CX + 3.2, 40.9, PRE_L, lw=1.8)

# Market context skip gate ────────────────────────────────────────────────────
diamond(CX + 3.2, 39.1, 3.2, 0.95, "SKIP?", SKIP)
v_arr(CX + 3.2, 39.9, 39.57, PRE_L, lw=1.8)
ax.text(CX + 5.1, 39.1, "YES → No trades today", fontsize=8.5, color=SKIP,
        fontfamily=FONT, va="center", fontstyle="italic")
ax.text(CX + 3.55, 38.55, "NO ↓", fontsize=8.5, color="#AAAAAA", fontfamily=FONT, va="center")

# Row D: News Intel ───────────────────────────────────────────────────────────
rbox(CX - 3.2, 38.6, 5.0, BH, "News Intel", "Earnings blackout · News sentiment context", PRE_L)
v_arr(CX - 3.2, 39.9, 39.1, PRE_L, lw=1.8)

# converge back to center
elbow(CX - 3.2, 38.1, CX, 38.1, PRE_L, lw=1.8)
elbow(CX + 3.2, 38.62, CX, 38.1, PRE_L, lw=1.8)

# Row E: Strategy Agent ───────────────────────────────────────────────────────
rbox(CX, 37.0, 6.5, BH,
     "Claude Strategy Agent",
     "claude-opus-4-7 · pool · rolling_score · above_vwap · signal_type",
     PRE_L, title_sz=10.5)
v_arr(CX, 38.1, 37.52, PRE_L, lw=2)

# per-stock context note
ax.text(CX + 4.2, 37.0,
        "Per-stock context:\npool · rolling_score\nabove_vwap · rs_vs_sector\natr_ratio · signal_type",
        fontsize=8, color=PRE_L, fontfamily=FONT, va="center", ha="left",
        linespacing=1.5, fontstyle="italic")

# Row F: Risk + Sector + Guardrails (3-up) ────────────────────────────────────
rbox(CX - 4.2, 35.2, 4.8, BH, "Risk Agent", "R:R >= 2.0 · Size $2.5K-$3.5K · Stop OK", PRE_L)
rbox(CX,       35.2, 4.8, BH, "Sector Guard", "Sector concentration cap", PRE_L)
rbox(CX + 4.2, 35.2, 4.8, BH, "Guardrails", "Duplicates · Price sanity · Loss limit", PRE_L)
v_arr(CX, 36.48, 35.7, PRE_L, lw=2)
elbow(CX, 35.7, CX - 4.2, 35.7, PRE_L, lw=1.8)
elbow(CX, 35.7, CX + 4.2, 35.7, PRE_L, lw=1.8)
elbow(CX - 4.2, 34.7, CX, 34.7, PRE_L, lw=1.8)
elbow(CX + 4.2, 34.7, CX, 34.7, PRE_L, lw=1.8)

# Row G: Alpaca + Supabase ────────────────────────────────────────────────────
rbox(CX, 33.6, 6.0, BH, "Alpaca Broker",
     "Bracket orders · strategy=b · Leg A +1% · Leg B +2%",
     "#0D2D5A", title_sz=10.5)
v_arr(CX, 34.7, 34.12, PRE_L, lw=2)

# DB write arrow
elbow(CX + 3.0, 33.6, CX + 6.5, 33.6, DB_L, lw=1.4)
ax.text(CX + 6.6, 33.6,
        "b_trade_plans\nb_planned_trades\nb_positions",
        fontsize=7.8, color=DB_L, fontfamily=FONT, va="center", ha="left")

# section-to-section
v_arr(CX, 33.08, 31.35, "#666666", lw=2, label="  positions open")


# ══════════════════════════════════════════════════════════════════════════════
# ② INTRADAY   y: 19.5 → 31.0
# ══════════════════════════════════════════════════════════════════════════════
section_bg(19.5, 31.0, INT)
section_hdr(30.5, "②  INTRADAY  —  every 30 min  (10:00 AM – 3:45 PM ET)", INT,
            right_note="orchestrator.py → intraday()")

# Position management row ─────────────────────────────────────────────────────
rbox(CX - 3.5, 29.4, 5.5, BH, "Reconcile + Refresh",
     "Detect bracket exits · Sync P&L · High watermark", INT_L)
rbox(CX + 3.5, 29.4, 5.5, BH, "Lock-in Logic",
     "Tier 1 $500: tighten trail · Tier 2 $700: close all", INT_L)
h_arr(CX - 0.75, CX + 0.75, 29.4, INT_L, lw=1.8)

# Guards for momentum scan ────────────────────────────────────────────────────
v_arr(CX, 28.9, 28.1, INT_L, lw=1.8)

rbox(CX - 4.0, 27.5, 4.5, 0.85, "Guards", "runs<6 · interval>90min\nslots open · loss OK", INT_L, title_sz=9.5)
rbox(CX + 4.0, 27.5, 4.5, 0.85, "Guards Pass?", "All 5 conditions met", INT_L, title_sz=9.5)
elbow(CX, 28.1, CX - 4.0, 27.92, INT_L, lw=1.8)
h_arr(CX - 1.75, CX + 1.75, 27.5, INT_L, lw=1.6, label="  check")
ax.text(CX + 6.6, 27.5, "NO → Skip\nmomentum scan", fontsize=8.5, color=SKIP,
        fontfamily=FONT, va="center", fontstyle="italic")
h_arr(CX + 6.25, CX + 6.55, 27.5, SKIP, lw=1.3)

# Intraday momentum scanner ───────────────────────────────────────────────────
v_arr(CX, 27.08, 26.28, INT_L, lw=1.8, label="  guards pass")
rbox(CX, 25.7, 6.5, BH, "Intraday Momentum Scanner",
     "SPY gate >=+0.5% · Pool 3: price >=+0.5% above VWAP · Max 6 runs · 90 min interval",
     INT_L, title_sz=10)

# SPY gate diamond ────────────────────────────────────────────────────────────
diamond(CX - 5.0, 25.7, 3.0, 0.9, "SPY\n>=+0.5%?", SKIP)
elbow(CX - 3.25, 25.7, CX - 3.53, 25.7, INT_L, lw=1.6)
ax.text(CX - 7.2, 25.7, "NO → Skip", fontsize=8.5, color=SKIP,
        fontfamily=FONT, va="center", fontstyle="italic")

# Candidates with pool field note
ax.text(CX + 4.0, 25.2,
        "Returns:\npool=2\nsignal_type=\nINTRADAY_MOMENTUM",
        fontsize=8, color=INT_L, fontfamily=FONT, va="center", ha="left",
        linespacing=1.5, fontstyle="italic")

# Mini pipeline: Strategy → Risk → Guardrails → Execute ──────────────────────
v_arr(CX, 25.2, 24.42, INT_L, lw=1.8)
rbox(CX - 4.2, 23.8, 4.5, 0.85, "Strategy", "claude-opus-4-7\nTime-of-day rules", INT_L, title_sz=9.5)
rbox(CX,       23.8, 4.5, 0.85, "Risk Agent", "R:R >=2.0 · +1% target", INT_L, title_sz=9.5)
rbox(CX + 4.2, 23.8, 4.5, 0.85, "Guardrails", "Duplicates · Loss limit", INT_L, title_sz=9.5)
elbow(CX, 24.42, CX - 4.2, 24.22, INT_L, lw=1.6)
h_arr(CX - 1.95, CX - 0.05, 23.8, INT_L, lw=1.5)
h_arr(CX + 2.3, CX + 1.95, 23.8, INT_L, lw=1.5)

# Note: INTRADAY_MOMENTUM bypasses 1pm restriction
ax.text(CX, 23.05,
        "INTRADAY_MOMENTUM signal bypasses 1pm pool restriction — confirmed movers valid at any hour",
        fontsize=8, color="#555555", fontfamily=FONT, va="center", ha="center",
        fontstyle="italic", zorder=5)

# Converge and execute
elbow(CX - 4.2, 23.37, CX, 22.8, INT_L, lw=1.5)
elbow(CX + 4.2, 23.37, CX, 22.8, INT_L, lw=1.5)

rbox(CX, 22.2, 5.5, BH, "Alpaca Broker",
     "Bracket orders · strategy=b · target +1%", "#0B2540", title_sz=10)

# DB write
elbow(CX + 2.75, 22.2, CX + 6.0, 22.2, DB_L, lw=1.3)
ax.text(CX + 6.1, 22.2, "b_positions\nb_daily_runs", fontsize=7.8,
        color=DB_L, fontfamily=FONT, va="center", ha="left")

# Loop-back arrow left spine
ax.plot([1.2, 1.2], [21.7, 29.4], color=INT_L, lw=1.8, zorder=3)
ax.annotate("", xy=(1.2, 29.4), xytext=(1.2, 29.39),
            arrowprops=dict(arrowstyle="-|>", color=INT_L, lw=1.8, mutation_scale=15), zorder=3)
elbow(1.2, 29.4, CX - 6.0, 29.4, INT_L, lw=1.8)
ax.text(0.5, 25.5, "repeat\nevery\n30 min", fontsize=9.5, color=INT_L,
        fontfamily=FONT, va="center", ha="center", fontweight="bold")

# section-to-section
v_arr(CX, 21.68, 20.35, "#666666", lw=2, label="  4:00 PM — market close")


# ══════════════════════════════════════════════════════════════════════════════
# ③ EOD   y: 11.5 → 20.0
# ══════════════════════════════════════════════════════════════════════════════
section_bg(11.5, 20.0, EOD)
section_hdr(19.5, "③  EOD  —  Post 4:00 PM ET", EOD,
            right_note="orchestrator.py → eod()")

# Close positions ─────────────────────────────────────────────────────────────
rbox(CX - 4.0, 18.4, 5.2, BH, "Close All Positions",
     "Market-sell remaining · Cancel bracket legs", EOD_L)
# Performance
rbox(CX + 4.0, 18.4, 5.2, BH, "Daily Performance",
     "P&L · win rate · count\nbest/worst trade", EOD_L)
h_arr(CX - 1.4, CX + 1.4, 18.4, EOD_L, lw=1.8)

# Pool Scorer ─────────────────────────────────────────────────────────────────
v_arr(CX, 17.9, 17.1, EOD_L, lw=1.8)
rbox(CX, 16.5, 8.0, 1.1,
     "Pool Scorer",
     "Score each Pool 3 stock: win/loss · P&L · slippage_bps · setup alignment\n"
     "7-day rolling_score → Promote (>=6) · Demote (<3) · Stay (3-6)",
     EOD_L, title_sz=10.5, sub_sz=8)

# Promote / demote branches ───────────────────────────────────────────────────
v_arr(CX, 15.95, 15.15, EOD_L, lw=1.8)

rbox(CX - 4.5, 14.6, 4.8, 0.9, "Promote to Pool 2",
     "rolling_score >= 6", EOD_L, title_sz=9.5)
rbox(CX,       14.6, 4.8, 0.9, "Stay in Pool",
     "3 <= score < 6", EOD_L, title_sz=9.5)
rbox(CX + 4.5, 14.6, 4.8, 0.9, "Demote to Pool 1",
     "rolling_score < 3", EOD_L, title_sz=9.5)
elbow(CX, 15.15, CX - 4.5, 14.97, EOD_L, lw=1.6)
v_arr(CX, 15.15, 14.97, EOD_L, lw=1.6)
elbow(CX, 15.15, CX + 4.5, 14.97, EOD_L, lw=1.6)

# Converge to DB write
elbow(CX - 4.5, 14.1, CX, 13.55, EOD_L, lw=1.5)
elbow(CX + 4.5, 14.1, CX, 13.55, EOD_L, lw=1.5)
v_arr(CX, 14.1, 13.55, EOD_L, lw=1.5)

ax.text(CX, 13.2, "b_stock_scores  ·  b_pools (updated membership)  ·  b_daily_performance",
        fontsize=9, color=DB_L, fontfamily=FONT, va="center", ha="center",
        fontweight="bold")

# section-to-section
v_arr(CX, 12.85, 12.35, "#666666", lw=2)


# ══════════════════════════════════════════════════════════════════════════════
# ④ INFRASTRUCTURE   y: 1.5 → 12.0
# ══════════════════════════════════════════════════════════════════════════════
section_bg(1.5, 12.0, DB)
section_hdr(11.5, "④  SUPABASE  +  INFRASTRUCTURE", DB)

# Supabase tables (3-up top row)
rbox(CX - 6.5, 10.3, 5.0, BH, "b_pools", "Pool membership · rolling_score", DB_L)
rbox(CX,       10.3, 5.0, BH, "b_positions", "Open/closed positions · run_id FK", DB_L)
rbox(CX + 6.5, 10.3, 5.0, BH, "b_stock_scores", "Daily behavioral scores", DB_L)

# Supabase tables (3-up second row)
rbox(CX - 6.5, 8.7, 5.0, BH, "b_trade_plans", "Daily trade plans", DB_L)
rbox(CX,       8.7, 5.0, BH, "b_planned_trades", "Individual planned trades", DB_L)
rbox(CX + 6.5, 8.7, 5.0, BH, "b_daily_runs", "One row per scan event", DB_L)

# Dashboard
rbox(CX, 7.1, 8.5, 1.1,
     "Streamlit Dashboard",
     "Pool composition · Pool 3 picks · P&L by pool · Position tracker · A vs B comparison",
     DB_L, title_sz=11)

for cx_t in [CX - 6.5, CX, CX + 6.5]:
    elbow(cx_t, 8.2, CX + (cx_t - CX)*0.07, 7.65, DB_L, lw=1.3)
    elbow(cx_t, 9.8, cx_t, 9.2, DB_L, lw=1.3)

# GitHub Actions + cron-job.org
rbox(CX - 5.5, 5.5, 7.5, 1.1,
     "GitHub Actions + cron-job.org",
     "Premarket 10:00 AM · Intraday every 30 min\nEOD post 4:00 PM · strategy=b tag",
     DB_L, title_sz=10)

# Alpaca
rbox(CX + 5.5, 5.5, 7.5, 1.1,
     "Alpaca Paper Trading",
     "Bracket orders (entry + take-profit + stop)\nAll orders tagged strategy=b",
     "#0D2D5A", title_sz=10)

elbow(CX - 5.5, 6.05, CX - 5.5, 6.55, DB_L, lw=1.3)
elbow(CX + 5.5, 6.05, CX + 5.5, 6.55, DB_L, lw=1.3)

ax.text(CX, 4.3,
        "Same Supabase project as Strategy A (b_ prefix)  ·  Same Alpaca account (strategy=b tag)",
        fontsize=8.5, color="#777777", fontfamily=FONT, va="center", ha="center",
        fontstyle="italic")


# ══════════════════════════════════════════════════════════════════════════════
# LEGEND
# ══════════════════════════════════════════════════════════════════════════════
legend_items = [
    (PRE_L, "Premarket step"),
    (INT_L, "Intraday step"),
    (EOD_L, "EOD / pool scoring step"),
    (DB_L,  "Infrastructure / Supabase"),
    (SKIP,  "Decision gate"),
]
lx, ly = 1.5, 3.1
ax.text(lx, ly + 0.5, "Legend", fontsize=9.5, fontweight="bold", color="#555555",
        fontfamily=FONT, va="center")
for i, (col, lbl) in enumerate(legend_items):
    bx = FancyBboxPatch((lx, ly - i*0.65 - 0.18), 0.65, 0.38,
                        boxstyle="round,pad=0.05,rounding_size=0.07",
                        facecolor=col, linewidth=0, zorder=5)
    ax.add_patch(bx)
    ax.text(lx + 0.85, ly - i*0.65, lbl, fontsize=8.5, color="#444444",
            fontfamily=FONT, va="center", zorder=5)


# ── Save ───────────────────────────────────────────────────────────────────────
out = "/Users/amitgarg/Claude Projects/trading-agent-b/workflow_diagram_b.png"
plt.savefig(out, dpi=DPI, bbox_inches="tight",
            facecolor=fig.get_facecolor(), edgecolor="none")
print(f"Saved: {out}")
plt.close()
