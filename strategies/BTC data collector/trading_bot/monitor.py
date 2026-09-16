# monitor.py
"""
Continuous bot monitor + runner.
- Runs BTC Fade bot at HH:02 every hour
- Runs ETH Momentum bot at HH:03 every hour
- Prints a health report every 20 minutes
- Alerts if no successful bot run in 60 minutes

Run this and leave it running:
    python monitor.py
"""
import os
import sys
import json
import time
import datetime
import subprocess
import threading

os.makedirs("logs",  exist_ok=True)
os.makedirs("state", exist_ok=True)

# ─── Constants ────────────────────────────────────────────────────────────────
BOT_DIR        = os.path.dirname(os.path.abspath(__file__))
PYTHON         = sys.executable
CHECK_INTERVAL = 20 * 60       # health report every 20 minutes
RUN_TIMEOUT    = 120            # max seconds to wait for a single bot run
NO_TRADE_WARN  = 60 * 60       # warn after 1 hour without a completed run

# ─── State ───────────────────────────────────────────────────────────────────
last_btc_run   = None   # datetime of last successful BTC run
last_eth_run   = None   # datetime of last successful ETH run
last_check     = None
run_count      = {"btc": 0, "eth": 0, "btc_err": 0, "eth_err": 0}
start_time     = datetime.datetime.now()
lock           = threading.Lock()


# ─── Utilities ───────────────────────────────────────────────────────────────

def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def elapsed_str(dt):
    if dt is None:
        return "never"
    secs = (datetime.datetime.now() - dt).total_seconds()
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m ago"
    elif m:
        return f"{m}m {s}s ago"
    return f"{s}s ago"

def load_state(name):
    path = os.path.join(BOT_DIR, "state", f"{name}_state.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

def tail_log(name, lines=10):
    path = os.path.join(BOT_DIR, "logs", f"{name}.log")
    if not os.path.exists(path):
        return "(no log file)"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-lines:]).strip()
    except Exception as e:
        return f"(error reading log: {e})"


# ─── Bot runner ──────────────────────────────────────────────────────────────

def run_bot(script_name, bot_key):
    """Run a bot script in a subprocess. Returns True on success."""
    global last_btc_run, last_eth_run
    script = os.path.join(BOT_DIR, script_name)
    print(f"\n[{now_str()}] >>> Starting {script_name} ...")
    try:
        result = subprocess.run(
            [PYTHON, script],
            cwd=BOT_DIR,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT,
            encoding="utf-8",
            errors="replace",
        )
        output = (result.stdout + result.stderr).strip()
        if output:
            for line in output.splitlines()[-20:]:  # last 20 lines
                print(f"    {line}")

        with lock:
            if result.returncode == 0:
                run_count[bot_key] += 1
                if bot_key == "btc":
                    last_btc_run = datetime.datetime.now()
                else:
                    last_eth_run = datetime.datetime.now()
                print(f"[{now_str()}] <<< {script_name} OK (run #{run_count[bot_key]})")
                return True
            else:
                run_count[f"{bot_key}_err"] += 1
                print(f"[{now_str()}] <<< {script_name} FAILED (exit code {result.returncode})")
                return False
    except subprocess.TimeoutExpired:
        run_count[f"{bot_key}_err"] += 1
        print(f"[{now_str()}] <<< {script_name} TIMEOUT after {RUN_TIMEOUT}s")
        return False
    except Exception as e:
        run_count[f"{bot_key}_err"] += 1
        print(f"[{now_str()}] <<< {script_name} ERROR: {e}")
        return False


# ─── Health report ────────────────────────────────────────────────────────────

def print_health():
    global last_check
    last_check = datetime.datetime.now()
    now = datetime.datetime.now()
    uptime_secs = (now - start_time).total_seconds()
    uptime_h = int(uptime_secs // 3600)
    uptime_m = int((uptime_secs % 3600) // 60)

    btc_st  = load_state("btc")
    eth_st  = load_state("eth")
    btc_pos = btc_st.get("open_trade")
    eth_pos = eth_st.get("open_trade")

    secs_since_btc = (now - last_btc_run).total_seconds() if last_btc_run else None
    secs_since_eth = (now - last_eth_run).total_seconds() if last_eth_run else None
    warn_btc = secs_since_btc is None or secs_since_btc > NO_TRADE_WARN
    warn_eth = secs_since_eth is None or secs_since_eth > NO_TRADE_WARN

    sep = "=" * 62
    print(f"\n{sep}")
    print(f"  HEALTH REPORT  [{now_str()}]  Uptime: {uptime_h}h {uptime_m}m")
    print(sep)

    # ── BTC Status ────────────────────────────────────────────────
    btc_icon = "WARNING" if warn_btc else "OK"
    print(f"\n  BTC Fade Bot [{btc_icon}]")
    print(f"    Last run : {elapsed_str(last_btc_run)}")
    print(f"    Runs OK  : {run_count['btc']}  |  Errors: {run_count['btc_err']}")
    print(f"    Last run : {btc_st.get('last_run', 'Never')}")
    print(f"    Balance  : ${btc_st.get('balance', 0):,.2f}")
    print(f"    Total PnL: ${btc_st.get('total_pnl', 0):+.2f}  Trades: {btc_st.get('total_trades', 0)}")
    if btc_pos:
        bars = btc_pos.get("bars_elapsed", 0)
        print(f"    OPEN TRADE: {btc_pos['direction']} @ ${btc_pos['entry_price']:,.2f} | {bars}h elapsed | Ticket: {btc_pos.get('ticket','?')}")
    else:
        print(f"    Position : No open trade")

    # ── ETH Status ────────────────────────────────────────────────
    eth_icon = "WARNING" if warn_eth else "OK"
    print(f"\n  ETH Momentum Bot [{eth_icon}]")
    print(f"    Last run : {elapsed_str(last_eth_run)}")
    print(f"    Runs OK  : {run_count['eth']}  |  Errors: {run_count['eth_err']}")
    print(f"    Last run : {eth_st.get('last_run', 'Never')}")
    print(f"    Balance  : ${eth_st.get('balance', 0):,.2f}")
    print(f"    Total PnL: ${eth_st.get('total_pnl', 0):+.2f}  Trades: {eth_st.get('total_trades', 0)}")
    if eth_pos:
        bars = eth_pos.get("bars_elapsed", 0)
        partial = " | Partial TP hit" if eth_pos.get("partial_filled") else ""
        print(f"    OPEN TRADE: {eth_pos['direction']} @ ${eth_pos['entry_price']:,.2f} | {bars}h elapsed{partial} | Ticket: {eth_pos.get('ticket','?')}")
    else:
        print(f"    Position : No open trade")

    # ── Warnings ──────────────────────────────────────────────────
    print()
    if warn_btc:
        gap = f"{int((secs_since_btc or 99999) // 60)}m" if secs_since_btc else "never"
        print(f"  [!] BTC bot has not completed a run in {gap}.")
        print(f"      Check logs/btc.log for errors.")
    if warn_eth:
        gap = f"{int((secs_since_eth or 99999) // 60)}m" if secs_since_eth else "never"
        print(f"  [!] ETH bot has not completed a run in {gap}.")
        print(f"      Check logs/eth.log for errors.")

    # ── Next scheduled run ─────────────────────────────────────────
    next_run_min = 60 - now.minute if now.minute < 60 else 0
    print(f"\n  Next hourly run in ~{next_run_min}m (at HH:02/HH:03)")
    print(f"  Next health check in {CHECK_INTERVAL // 60}m")
    print(sep + "\n")

    # ── If no runs at all in 1 hour: dump last log lines ──────────
    if warn_btc and run_count["btc"] == 0 and run_count["btc_err"] > 0:
        print("  === Last BTC log lines ===")
        print(tail_log("btc", 15))
        print()
    if warn_eth and run_count["eth"] == 0 and run_count["eth_err"] > 0:
        print("  === Last ETH log lines ===")
        print(tail_log("eth", 15))
        print()


# ─── Scheduling ──────────────────────────────────────────────────────────────

def should_run_btc(now):
    """Run BTC at HH:02. Returns True if now is within the :02 minute."""
    return now.minute == 2

def should_run_eth(now):
    """Run ETH at HH:03. Returns True if now is within the :03 minute."""
    return now.minute == 3


# Track which hour we last ran each bot to avoid double-running
_last_btc_hour = -1
_last_eth_hour = -1


def main():
    global _last_btc_hour, _last_eth_hour

    print(f"\n{'=' * 62}")
    print(f"  BTC FADE + ETH MOMENTUM MONITOR")
    print(f"  Started: {now_str()}")
    print(f"  Health check: every {CHECK_INTERVAL // 60} minutes")
    print(f"  BTC runs at: HH:02 | ETH runs at: HH:03")
    print(f"{'=' * 62}\n")

    # Run both bots immediately on startup for a first check
    print("[STARTUP] Running both bots now for initial check...")
    run_bot("run_btc.py", "btc")
    time.sleep(3)
    run_bot("run_eth.py", "eth")

    # Print first health report
    print_health()

    last_health_time = datetime.datetime.now()

    while True:
        now = datetime.datetime.now()
        current_hour = now.hour

        # ── Hourly scheduled runs ─────────────────────────────────
        if should_run_btc(now) and _last_btc_hour != current_hour:
            _last_btc_hour = current_hour
            run_bot("run_btc.py", "btc")

        if should_run_eth(now) and _last_eth_hour != current_hour:
            _last_eth_hour = current_hour
            run_bot("run_eth.py", "eth")

        # ── Health check every 20 minutes ─────────────────────────
        since_health = (now - last_health_time).total_seconds()
        if since_health >= CHECK_INTERVAL:
            print_health()
            last_health_time = now

        time.sleep(30)  # poll every 30 seconds


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n[{now_str()}] Monitor stopped. Goodbye!")
