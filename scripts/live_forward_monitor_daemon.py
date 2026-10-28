import os
import sys
import time
from pathlib import Path
from datetime import datetime
import MetaTrader5 as mt5

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

MAGIC_NUMBERS = {20261006, 20261007, 20261008, 20261009, 20261010}
TERMINAL_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
LOG_FILE = Path(r"f:\Tete\SAJIM STRATEGIES\logs\forward_run_events.log")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
COMMON_JOURNAL = Path(r"C:\Users\sajim\AppData\Roaming\MetaQuotes\Terminal\Common\Files\MCP_Scalper_Journal.csv")

def log_event(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {message}"
    print(formatted, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(formatted + "\n")

log_event("=== Live Forward Multi-Asset Monitor Daemon Started ===")
log_event("Monitoring Symbols: Portfolio (XAUUSD, US30, USDJPY, GBPUSD, etc.)")

# Initial connection
connected = False
while not connected:
    if mt5.initialize():
        connected = True
    else:
        log_event(f"[WAITING] MT5 initializing... (Error: {mt5.last_error()})")
        time.sleep(5.0)

acc = mt5.account_info()
if acc:
    log_event(f"Connected: Account {acc.login} ({acc.name}) | Server: {acc.server} | Balance: ${acc.balance:,.2f} | Equity: ${acc.equity:,.2f}")

known_positions = {}
prev_basket_count = 0
last_heartbeat = time.time()
prev_balance = acc.balance if acc else 0.0
was_disconnected = False
last_journal_size = COMMON_JOURNAL.stat().st_size if COMMON_JOURNAL.exists() else 0

try:
    while True:
        time.sleep(2.0)
        
        # 1. Verify MT5 connection status
        t_info = mt5.terminal_info()
        if not t_info or not t_info.connected:
            if not was_disconnected:
                log_event("[WARNING] MT5 broker network connection lost. Waiting for terminal to reconnect...")
                was_disconnected = True
            time.sleep(5.0)
            if not t_info:
                mt5.initialize()
            continue
        else:
            if was_disconnected:
                acc_reconnected = mt5.account_info()
                login_id = acc_reconnected.login if acc_reconnected else "unknown"
                log_event(f"[INFO] MT5 connection restored (Account: {login_id}).")
                was_disconnected = False
            
        # 2. Tail EA's Common CSV Journal if new rows were written
        if COMMON_JOURNAL.exists():
            try:
                curr_size = COMMON_JOURNAL.stat().st_size
                if curr_size > last_journal_size:
                    with open(COMMON_JOURNAL, "r", encoding="utf-8", errors="ignore") as jf:
                        jf.seek(last_journal_size)
                        new_data = jf.read()
                        last_journal_size = curr_size
                        for line in new_data.strip().splitlines():
                            if line and not line.startswith("Timestamp"):
                                log_event(f"[EA FORENSIC] {line}")
            except Exception as e:
                pass

        # 3. Inspect active positions across portfolio
        positions = mt5.positions_get()
        our_positions = [p for p in positions if p.magic in MAGIC_NUMBERS or (p.comment and "MCP_Scalp" in p.comment)] if positions else []
        current_tickets = {p.ticket for p in our_positions}
        
        # Check positions for new entries or partial closes / breakeven moves
        for p in our_positions:
            p_type = "BUY" if p.type == 0 else "SELL"
            if p.ticket not in known_positions:
                known_positions[p.ticket] = {'volume': p.volume, 'sl': p.sl, 'type': p_type, 'symbol': p.symbol}
                log_event(f"[NEW POSITION] Ticket #{p.ticket} | {p.symbol} {p_type} {p.volume:.2f} lots @ {p.price_open:.2f} | SL: {p.sl:.2f} | TP: {p.tp:.2f}")
            else:
                prev_info = known_positions[p.ticket]
                # Detect partial close
                if p.volume < prev_info['volume'] - 0.001:
                    closed_vol = prev_info['volume'] - p.volume
                    log_event(f"[PARTIAL CLOSE] Ticket #{p.ticket} ({p.symbol}) | Closed {closed_vol:.2f} lots | Remaining: {p.volume:.2f} lots | SL: {p.sl:.2f}")
                    prev_info['volume'] = p.volume
                # Detect breakeven / SL modification
                if abs(p.sl - prev_info['sl']) > 0.01:
                    log_event(f"[SL MODIFIED] Ticket #{p.ticket} ({p.symbol}) | SL moved from {prev_info['sl']:.2f} to {p.sl:.2f}")
                    prev_info['sl'] = p.sl
                
        # Detect closed basket / all positions exited
        if prev_basket_count > 0 and len(our_positions) == 0:
            known_positions.clear()
            
            acc_now = mt5.account_info()
            curr_balance = acc_now.balance if acc_now else prev_balance
            delta_balance = curr_balance - prev_balance
            prev_balance = curr_balance
            
            log_event(f"[PORTFOLIO BASKET CLOSED] All positions exited. Net Realized: ${delta_balance:+,.2f} | Balance: ${curr_balance:,.2f}")
            
        prev_basket_count = len(our_positions)
        
        # 4. Periodic Heartbeat (Every 5 minutes)
        if time.time() - last_heartbeat >= 300.0:
            last_heartbeat = time.time()
            acc_now = mt5.account_info()
            equity = acc_now.equity if acc_now else 0.0
            
            if len(our_positions) == 0:
                gold_sym = mt5.symbol_info("XAUUSD")
                g_spread = gold_sym.spread if gold_sym else 0
                g_bid = gold_sym.bid if gold_sym else 0.0
                log_event(f"[HEARTBEAT] Portfolio IDLE | Gold: {g_bid:.2f} (Spr: {g_spread}) | Balance: ${prev_balance:,.2f} | Equity: ${equity:,.2f}")
            else:
                total_pnl = sum(p.profit for p in our_positions)
                pos_summary = ", ".join([f"{p.symbol} {p.volume}L" for p in our_positions])
                log_event(f"[HEARTBEAT] ACTIVE POSITIONS ({len(our_positions)}: {pos_summary}) | Floating PnL: ${total_pnl:+,.2f} | Equity: ${equity:,.2f}")

except KeyboardInterrupt:
    log_event("Monitor daemon stopped by user.")
finally:
    mt5.shutdown()
