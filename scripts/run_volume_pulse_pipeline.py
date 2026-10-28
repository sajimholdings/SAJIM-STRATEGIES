import os
import sys
import time
import subprocess
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, r"f:\Tete\SAJIM STRATEGIES")

from sajim_quant.adapters.mql5.mt5_adapter import MT5HeadlessAdapter
from sajim_quant.core.schema import BacktestResult, RunMetadata, Trade
from sajim_quant.analytics import (
    calculate_metrics,
    generate_text_tear_sheet,
    generate_markdown_tear_sheet,
)

print("=== Launching Option A (Recent 1 Month) Backtest: MCP_VolumePulse_Sajim ===")

# 1. Enable automated terminal restart
os.environ["SAJIM_MT5_KILL_TERMINAL"] = "1"

# Clear stale cached tester profile for Volume Pulse
subprocess.run([
    "powershell", "-NoProfile", "-Command",
    "Remove-Item 'C:\\Users\\sajim\\AppData\\Roaming\\MetaQuotes\\Terminal\\D0E8209F77C8CF37AD8BF550E51FF075\\MQL5\\Profiles\\Tester\\MCP_VolumePulse*' -Force -ErrorAction SilentlyContinue; "
    "Remove-Item 'C:\\Users\\sajim\\AppData\\Roaming\\MetaQuotes\\Terminal\\D0E8209F77C8CF37AD8BF550E51FF075\\Tester\\cache\\MCP_VolumePulse*' -Force -ErrorAction SilentlyContinue"
], check=False)

source_mq5 = Path(r"f:\Tete\SAJIM STRATEGIES\strategies\MCP_VolumePulse_Sajim\MCP_VolumePulse_Sajim.mq5")
assert source_mq5.exists(), f"Source file not found at {source_mq5}"

adapter = MT5HeadlessAdapter(
    mq5_source_path=source_mq5,
    strategy_name="MCP_VolumePulse_Sajim",
)

# 2. Run Strategy Tester on XAUUSD M1 for 2026-09-10 to 2026-10-06 (Every tick)
print("Executing MT5 Headless Strategy Tester (XAUUSD M1 | Every Tick | 2026.09.10 - 2026.10.06)...")
t0 = time.time()

try:
    result = adapter.run(
        symbol="XAUUSD",
        timeframe="M1",
        start_date="2026-09-10",
        end_date="2026-10-06",
        initial_balance=10000.0,
        timeout_seconds=600,
        model=0,  # 0 = Every tick
        InpLot_Tier1=0.10,
        InpLot_Tier2=0.07,
        InpLot_Tier3=0.03,
        InpMinAddonPoints=10,
        InpMinAddonIntervalSec=3,
        InpVelocityWindow_ms=3000,
        InpVelocityTrigger=20,
        InpVelocityContinuation=14,
        InpDirectionThreshold=0.70,
        InpDecelExitRatio=0.35,
        InpStagnationSeconds=10,
        InpRetracementExitPoints=15,
        InpEmergencySL_Points=35,
        InpUseM5CandleClock=1,
        InpMaxM5ElapsedSec=180,
        InpMaxSpreadPoints=50,
        InpSessionStartHour=0,
        InpSessionEndHour=24,
    )
    elapsed = time.time() - t0
    print(f"Backtest completed in {elapsed:.1f}s!")
    print(f"Total trades harvested: {result.total_trades}")

    # 3. Process Analytics
    results_dir = Path(r"f:\Tete\SAJIM STRATEGIES\strategies\MCP_VolumePulse_Sajim\results")
    results_dir.mkdir(parents=True, exist_ok=True)

    if result.total_trades > 0:
        metrics = calculate_metrics(result)
        print("\n--- PERFORMANCE METRICS (RECENT 1 MONTH) ---")
        print(f"Net Profit:          ${metrics.net_profit:,.2f}")
        print(f"Total Return:        {metrics.return_pct:.2f}%")
        print(f"Win Rate:            {metrics.win_rate_pct:.1f}%")
        print(f"Profit Factor:       {metrics.profit_factor:.2f}")
        print(f"Max Drawdown:        ${metrics.max_drawdown_amount:,.2f} ({metrics.max_drawdown_pct:.1f}%)")
        print(f"Sharpe Ratio:        {metrics.sharpe_ratio:.2f}")
        print(f"Sortino Ratio:       {metrics.sortino_ratio:.2f}")
        print(f"Total Trades:        {metrics.total_trades}")
        print(f"Winning Trades:      {metrics.winning_trades}")
        print(f"Losing Trades:       {metrics.losing_trades}")
        print(f"Avg Trade PnL:       ${metrics.avg_trade_pnl:,.2f}")
        print(f"Avg Win:             ${metrics.avg_win:,.2f}")
        print(f"Avg Loss:            ${metrics.avg_loss:,.2f}")
        if metrics.avg_loss != 0:
            print(f"Realized Win/Loss:   {abs(metrics.avg_win / metrics.avg_loss):.2f}:1")
        print(f"Max Consecutive Win: {metrics.max_consecutive_wins}")
        print(f"Max Consecutive Loss:{metrics.max_consecutive_losses}")

        # Generate ASCII Tear Sheet
        tear_sheet = generate_text_tear_sheet(metrics)
        print("\n" + tear_sheet)

        # Export Markdown Tear Sheet
        md_report = generate_markdown_tear_sheet(
            metrics,
            assumptions={
                "engine": "MT5 Strategy Tester (Headless)",
                "symbol": "XAUUSD",
                "timeframe": "M1",
                "period": "2026.09.10 - 2026.10.06 (Recent 1 Month)",
                "initial_deposit": "$10,000.00",
                "tick_model": "Every tick (Simulated Real)",
                "spread": "Broker floating (max 5.0 pips)",
                "pyramid_structure": "0.10 -> 0.07 -> 0.03 (Max 0.20 lots)",
                "exit_mechanism": "Dynamic Volume Drop & 10s Stagnation Clock",
            }
        )
        (results_dir / "tear_sheet.md").write_text(md_report, encoding="utf-8")
        print(f"[PASS] Saved Markdown Tear Sheet -> {results_dir / 'tear_sheet.md'}")

        # Export SAJIM JSON Result
        json_path = results_dir / "backtest_recent.json"
        result.save_json(json_path)
        print(f"[PASS] Saved Universal Schema JSON -> {json_path}")

        # Export Copy of Trades CSV
        csv_dest = results_dir / "trades.csv"
        result.save_trades_csv(csv_dest)
        print(f"[PASS] Saved Trades CSV -> {csv_dest}")
    else:
        print("[WARNING] Strategy produced 0 trades. Checking tester logs...")

finally:
    # 4. Reopen MT5 terminal for user convenience
    print("\nRestoring MT5 terminal in background...")
    subprocess.Popen([r"C:\Program Files\MetaTrader 5\terminal64.exe"])
    print("MT5 terminal restored.")
