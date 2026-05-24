"""
One-off script: diagnose and fix stale JNJ open position in Strategy B.
Run: python3 fix_jnj_stale.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

import core.db as db
from agents.alpaca_broker import _get as alpaca_client, get_open_tickers

# ── 1. Check Supabase ────────────────────────────────────────
rows = db.select("b_positions", filters={"ticker": "JNJ", "status": "OPEN"})
if not rows:
    print("✅ No OPEN JNJ row in b_positions — nothing to fix.")
    sys.exit(0)

jnj = rows[0]
print(f"Supabase b_positions OPEN row for JNJ:")
print(f"  id:           {jnj['id']}")
print(f"  entry_price:  {jnj['entry_price']}")
print(f"  fill_price:   {jnj.get('fill_price')}")
print(f"  shares:       {jnj['shares']}")
print(f"  date:         {jnj.get('date')}")
print()

# ── 2. Check Alpaca ──────────────────────────────────────────
broker = alpaca_client()
alpaca_open = get_open_tickers()
print(f"Alpaca open tickers: {alpaca_open}")
jnj_in_alpaca = "JNJ" in alpaca_open

if jnj_in_alpaca:
    print("⚠️  JNJ is STILL OPEN in Alpaca paper account (overnight position).")
    print("   Market is closed — close will be attempted anyway (may queue for Monday open).")
    try:
        broker.close_position("JNJ")
        print("   close_position(JNJ) submitted to Alpaca.")
    except Exception as e:
        print(f"   close_position failed: {e} — will mark closed in Supabase at last known price.")
else:
    print("✅ JNJ is NOT in Alpaca — close order already executed there. Supabase just wasn't updated.")

# ── 3. Fetch last known close price ─────────────────────────
import yfinance as yf
try:
    hist = yf.Ticker("JNJ").history(period="2d")
    last_price = float(hist["Close"].iloc[-1])
    print(f"Last known JNJ close price (yfinance): ${last_price:.2f}")
except Exception as e:
    last_price = float(jnj.get("current_price") or jnj["entry_price"])
    print(f"yfinance failed ({e}) — using entry_price as fallback: ${last_price:.2f}")

# ── 4. Compute P&L and close in Supabase ────────────────────
entry   = float(jnj.get("fill_price") or jnj["entry_price"])
shares  = int(jnj["shares"])
pnl     = round(shares * (last_price - entry), 2)

print(f"\nComputed P&L: {shares} shares × (${last_price:.2f} − ${entry:.2f}) = ${pnl:.2f}")
confirm = input("\nMark JNJ as CLOSED in Supabase with these values? [y/N] ").strip().lower()

if confirm == "y":
    db.update("b_positions", {"id": jnj["id"]}, {
        "status":         "CLOSED",
        "close_price":    last_price,
        "realized_pnl":   pnl,
        "close_reason":   "EOD",
        "exit_mechanism": "EOD",
    })
    print(f"✅ JNJ marked CLOSED — P&L ${pnl:+.2f}")
else:
    print("Skipped — no changes made.")
