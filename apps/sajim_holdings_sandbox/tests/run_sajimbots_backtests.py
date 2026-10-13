import sys
from pathlib import Path
import logging
import pandas as pd

# Add root to pythonpath
root_dir = Path(__file__).resolve().parents[3]
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))

from sajim_quant.adapters.mql5.mt5_adapter import MT5HeadlessAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_sajimbots")

def calculate_metrics(trades):
    if not trades:
        return {"Trades": 0, "Win Rate": "0%", "PF": 0, "Net PnL": 0, "Expectancy": 0}
    
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    
    gross_profit = sum(t.net_pnl for t in wins)
    gross_loss = abs(sum(t.net_pnl for t in losses))
    net_pnl = sum(t.net_pnl for t in trades)
    
    win_rate = len(wins) / len(trades) * 100
    pf = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    expectancy = (gross_profit - gross_loss) / len(trades)
    
    return {
        "Trades": len(trades),
        "Win Rate": f"{win_rate:.1f}%",
        "PF": round(pf, 2),
        "Net PnL": round(net_pnl, 2),
        "Expectancy": round(expectancy, 2)
    }

def main():
    sandbox_mt5_dir = Path(__file__).resolve().parents[1] / "MT5_Sajim"
    eas_dir = sandbox_mt5_dir / "MQL5" / "Experts" / "SajimBots"
    
    terminal_exe = sandbox_mt5_dir / "terminal64.exe"
    metaeditor_exe = sandbox_mt5_dir / "metaeditor64.exe"
    
    # In portable mode, the common files where reports are written usually reside in the Terminal Data Folder.
    # However, for MT5, FileOpen with FILE_COMMON writes to Terminal\Common\Files regardless of portable mode in some setups,
    # but let's test if the adapter can pick it up. The adapter reads from `common_files_path / "sajim_trades.csv"`.
    # Usually in portable mode, "Common" is inside the sandbox.
    common_files = sandbox_mt5_dir / "Common" / "Files"
    if not common_files.exists():
        common_files.mkdir(parents=True, exist_ok=True)
    
    eas_to_test = [
        "EMA_Pullback_TrendDuration_EA.mq5",
        "CrossHedgeMicroScalper.mq5",
        "BurstLogger.mq5",
        "ExpertMACD.mq5"
    ]
    
    symbols_to_test = [
        "EURUSD", "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "AUDUSD", "NZDUSD", # Majors
        "GBPJPY", "EURJPY", "EURGBP", # Crosses
        "XAUUSD", "XAGUSD", # Metals
        "BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD", # Crypto
        "US30", "US500", "GER40", "UK100" # Indices
    ]
    
    results = {}
    
    for ea_file in eas_to_test:
        results[ea_file] = {}
        ea_path = eas_dir / ea_file
        
        if not ea_path.exists():
            logger.error(f"EA not found: {ea_path}")
            continue
            
        for symbol in symbols_to_test:
            logger.info(f"\n{'='*50}\nTesting: {ea_file} on {symbol}\n{'='*50}")
            try:
                adapter = MT5HeadlessAdapter(
                    mq5_source_path=ea_path,
                    strategy_name=ea_file.replace(".mq5", ""),
                    terminal_exe_path=terminal_exe,
                    common_files_path=common_files
                )
                
                # Override preprocessor paths for the sandbox
                adapter.preprocessor.metaeditor_path = metaeditor_exe
                adapter.preprocessor.mt5_data_path = sandbox_mt5_dir
                adapter.preprocessor.include_dir = sandbox_mt5_dir / "MQL5" / "Include"
                adapter.preprocessor.experts_dir = sandbox_mt5_dir / "MQL5" / "Experts" / "Sajim"
                
                # Run test
                res = adapter.run(
                    symbol=symbol,
                    timeframe="H1",
                    start_date="2026-01-01",
                    end_date="2026-07-01",
                    initial_balance=10000,
                    use_custom_symbol=False,
                    sync_data=False,
                    timeout_seconds=600
                )
                
                metrics = calculate_metrics(res.trades)
                results[ea_file][symbol] = metrics
                logger.info(f"Results for {ea_file} on {symbol}: {metrics}")
                
            except Exception as e:
                logger.exception(f"Failed to test {ea_file} on {symbol}: {e}")
                results[ea_file][symbol] = {"Error": str(e)}

    print("\n\n=== FINAL RESULTS SUMMARY ===")
    for ea, sym_results in results.items():
        print(f"\n--- {ea} ---")
        df = pd.DataFrame.from_dict(sym_results, orient="index")
        print(df.to_string())

if __name__ == "__main__":
    main()
