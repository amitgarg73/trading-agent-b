"""
Blue chip universe for Strategy B.

Pool 2 seed: 40 curated large-cap stocks with high liquidity, predictable
behavioral patterns, and strong institutional coverage. Organized by sector
for diversification awareness.

Pool 1 is the broader S&P 500 liquid universe (~200 names, avg vol > 5M).
Stocks can be promoted from Pool 1 → Pool 2 based on 7-day rolling score.
"""

# --- Mag 7 ---
MAG_7 = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"]

# --- Financials ---
FINANCIALS = ["JPM", "GS", "V", "MA", "BAC", "PYPL"]

# --- Healthcare ---
HEALTHCARE = ["UNH", "LLY", "JNJ", "ISRG", "VRTX"]

# --- Energy / Materials ---
ENERGY = ["XOM", "CVX", "FCX"]

# --- Consumer ---
CONSUMER = ["WMT", "COST", "HD", "SHOP"]

# --- Tech / Semis / Growth ---
TECH_GROWTH = ["AMD", "AVGO", "NFLX", "CRM", "ORCL", "ARM", "MRVL"]

# --- Enterprise / Cyber / AI ---
ENTERPRISE_AI = ["PANW", "CRWD", "NOW", "PLTR", "UBER"]

# Pool 2 seed — 40 blue chips, starts in pool 2 on day 1
POOL_2_SEED: list[str] = MAG_7 + FINANCIALS + HEALTHCARE + ENERGY + CONSUMER + TECH_GROWTH + ENTERPRISE_AI

# Pool 1 broader liquid universe — top S&P 500 by avg daily volume > 5M
# Excludes Pool 2 seed (they start in pool 2 directly)
POOL_1_UNIVERSE: list[str] = [
    "SPY", "QQQ", "IWM",
    # Large cap tech
    "INTC", "QCOM", "MU", "AMAT", "LRCX", "KLAC", "MRVL", "TXN", "ADI",
    # Financials
    "MS", "WFC", "C", "AXP", "BLK", "SCHW", "USB",
    # Healthcare / Pharma
    "ABBV", "MRK", "PFE", "BMY", "AMGN", "GILD", "CVS", "CI",
    # Consumer / Retail
    "TGT", "AMZN", "LOW", "NKE", "SBUX", "MCD", "YUM",
    # Industrials
    "CAT", "DE", "HON", "RTX", "LMT", "BA", "GE", "MMM",
    # Energy
    "SLB", "OXY", "COP", "PSX", "VLO",
    # Utilities / REITs
    "NEE", "DUK", "SO", "AMT", "PLD",
    # Communication
    "DIS", "CMCSA", "NFLX", "T", "VZ",
    # ETFs for sector exposure
    "XLF", "XLK", "XLV", "XLE", "XLY", "XLI", "XLB",
]

# All tickers Strategy B ever scans (Pool 1 + Pool 2 seed)
ALL_TICKERS: list[str] = list(dict.fromkeys(POOL_2_SEED + POOL_1_UNIVERSE))

# Sector mapping — used for diversification checks and scoring context
SECTOR_MAP: dict[str, str] = {
    # Mag 7
    "AAPL": "Technology", "MSFT": "Technology", "GOOGL": "Technology",
    "AMZN": "Consumer Discretionary", "META": "Technology",
    "NVDA": "Technology", "TSLA": "Consumer Discretionary",
    # Financials
    "JPM": "Financials", "GS": "Financials", "V": "Financials",
    "MA": "Financials", "BAC": "Financials", "MS": "Financials",
    "WFC": "Financials", "C": "Financials", "AXP": "Financials",
    "BLK": "Financials", "SCHW": "Financials",
    # Healthcare
    "UNH": "Healthcare", "LLY": "Healthcare", "JNJ": "Healthcare",
    "ABBV": "Healthcare", "MRK": "Healthcare", "PFE": "Healthcare",
    "BMY": "Healthcare", "AMGN": "Healthcare", "GILD": "Healthcare",
    # Energy
    "XOM": "Energy", "CVX": "Energy", "SLB": "Energy",
    "OXY": "Energy", "COP": "Energy",
    # Consumer
    "WMT": "Consumer Staples", "COST": "Consumer Staples",
    "HD": "Consumer Discretionary", "TGT": "Consumer Discretionary",
    "NKE": "Consumer Discretionary", "SBUX": "Consumer Discretionary",
    "MCD": "Consumer Discretionary",
    # Tech / Semis
    "AMD": "Technology", "AVGO": "Technology", "INTC": "Technology",
    "QCOM": "Technology", "MU": "Technology", "AMAT": "Technology",
    "TXN": "Technology", "CRM": "Technology", "ORCL": "Technology",
    "ARM": "Technology", "MRVL": "Technology",
    # Enterprise / Cyber / AI
    "PANW": "Technology", "CRWD": "Technology", "NOW": "Technology",
    "PLTR": "Technology", "UBER": "Consumer Discretionary",
    # New Healthcare
    "ISRG": "Healthcare", "VRTX": "Healthcare",
    # New Fintech
    "PYPL": "Financials",
    # New Consumer
    "SHOP": "Consumer Discretionary",
    # New Materials
    "FCX": "Materials",
    # Communication
    "NFLX": "Communication", "DIS": "Communication",
    "CMCSA": "Communication", "T": "Communication", "VZ": "Communication",
    # Industrials
    "CAT": "Industrials", "DE": "Industrials", "HON": "Industrials",
    "BA": "Industrials", "GE": "Industrials", "LMT": "Industrials",
    # ETFs
    "SPY": "ETF", "QQQ": "ETF", "IWM": "ETF",
    "XLF": "ETF", "XLK": "ETF", "XLV": "ETF",
    "XLE": "ETF", "XLY": "ETF", "XLI": "ETF",
}

# Sector ETF map — used to compute relative strength vs sector
SECTOR_ETF: dict[str, str] = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Energy": "XLE",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Communication": "XLC",
    "ETF": "SPY",
}
