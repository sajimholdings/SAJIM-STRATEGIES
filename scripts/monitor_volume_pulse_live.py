import time
import os
import glob
from pathlib import Path
from datetime import datetime
import MetaTrader5 as mt5

MAGIC_NUMBER = 20261007
SYMBOL = "XAUUSD"

print("======================================================================")
print("     SAJIM QUANT: LIVE FORWARD RUN SENTINEL (MCP_VolumePulse_Sajim)   ")
print("======================================================================")

if not mt5.initialize():
    print("[ERROR] Could not connect to MetaTrader 5:", mt5.last_error())
    exit(1)

acc = mt5.account_info()
term = mt5.terminal_info()

if not acc or not term:
    print("[ERROR] Account or Terminal info unavailable.")
    mt5.shutdown()
    exit(1)

print(f"Connected to Account: {acc.login} ({acc.name}) | Server: {acc.server}")
print(f"Balance: ${acc.balance:,.2f} | Equity: ${acc.equity:,.2f}")
print(f"Algo Trading Enabled (Terminal): {term.trade_allowed}")
print(f"Algo Trading Enabled (Account):  {acc.trade_expert}")
print("-" * 70)

# Check active quote
sym = mt5.symbol_info(SYMBOL)
if sym:
    print(f"Symbol: {SYMBOL} | Live Bid: {sym.bid:.2f} | Ask: {sym.ask:.2f} | Spread: {sym.spread} pts")
print("-" * 70)

# Check active positions for this strategy
positions = mt5.positions_get(symbol=SYMBOL)
our_positions = [p for p in positions if p.magic == MAGIC_NUMBER] if positions else []

print(f"Active Basket Positions (Magic {MAGIC_NUMBER}): {len(our_positions)}")
if our_positions:
    total_pnl = sum(p.profit for p in our_positions)
    print(f"Total Basket Unrealized PnL: ${total_pnl:+,.2f}")
    for p in our_positions:
        p_type = "BUY" if p.type == 0 else "SELL"
        print(f"  -> Ticket #{p.ticket}: {p_type} {p.volume} lots @ {p.price_open:.2f} | Current: {p.price_current:.2f} | PnL: ${p.profit:+,.2f}")
else:
    print("Basket is currently IDLE. Waiting for M1 tick velocity ignition surge...")

# Read latest lines from MQL5 Experts Log
data_path = Path(r"C:\Users\sajim\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075")
mql_logs = sorted(glob.glob(str(data_path / "MQL5" / "Logs" / "*.log")))
if mql_logs:
    latest_log = mql_logs[-1]
    print(f"\n--- Recent EA Log Messages ({os.path.basename(latest_log)}) ---")
    try:
        with open(latest_log, "r", encoding="utf-16", errors="ignore") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
            for l in lines[-8:]:
                print(" ", l)
    except Exception as e:
        print(f"Could not read log: {e}")

print("======================================================================")
