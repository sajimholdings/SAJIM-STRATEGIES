import os
import pandas as pd
from volatility_breakout_backtester import VolatilityBreakoutBacktester
import sys

# Force UTF-8 output so Unicode symbols print on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

instruments = [
    ("data/BTCUSDT_1H.csv", 0.001),
    ("data/BTCUSDT_15M.csv", 0.001),
    ("data/ETHUSDT_1H.csv", 0.001)
]

body_multipliers = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
trailing_multipliers = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]

for file_path, fee in instruments:
    instrument_name = os.path.basename(file_path)
    print(f"\n{'='*60}")
    print(f"Sweeping {instrument_name}...")
    print(f"{'='*60}")
    
    results_list = []
    
    # Pre-load the DataFrame to save parsing time for the 49 combinations
    tester = VolatilityBreakoutBacktester()
    df = pd.read_csv(file_path)
    df = df.rename(columns={
        "datetime_utc": "Datetime",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close"
    })
    df["Datetime"] = pd.to_datetime(df["Datetime"])
    df = df.set_index("Datetime")
    df = tester.calculate_indicators(df)
    df = df.dropna()
    
    count = 0
    total = len(body_multipliers) * len(trailing_multipliers)
    
    for body_m in body_multipliers:
        for trailing_m in trailing_multipliers:
            count += 1
            if count % 10 == 0:
                print(f"  Progress: {count}/{total} configurations tested...")
                
            # Create a fresh backtester for each run to ensure clean state
            run_tester = VolatilityBreakoutBacktester()
            res = run_tester.run_backtest(
                file_path=file_path,
                body_atr_multiplier=body_m,
                trailing_stop_atr_multiplier=trailing_m,
                fee=fee,
                df=df.copy() # pass a copy so we don't modify the pre-loaded df
            )
            
            # Store parameters with results
            res["body_atr"] = body_m
            res["trailing_atr"] = trailing_m
            results_list.append(res)
            
    # Sort by Profit Factor and Expectancy
    results_list.sort(key=lambda x: (x["profit_factor"], x["expectancy_r"]), reverse=True)
    
    print(f"\n--- TOP 5 CONFIGURATIONS FOR {instrument_name} ---")
    
    # Ensure we filter out infinite profit factors resulting from zero losses
    valid_results = [r for r in results_list if r["profit_factor"] != float('inf') and r["total_trades"] > 30]
    
    if not valid_results:
         # Fallback if all configs generated fewer than 30 trades or had no losses
         valid_results = [r for r in results_list if r["profit_factor"] != float('inf')]
         
    for i, res in enumerate(valid_results[:5]):
        print(f"Rank {i+1}: Body Trigger={res['body_atr']}x, Trailing Stop={res['trailing_atr']}x")
        print(f"  Profit Factor: {res['profit_factor']:.3f}")
        print(f"  Expectancy (R): {res['expectancy_r']:.3f}")
        print(f"  Win Rate: {res['win_rate']*100:.2f}%")
        print(f"  Total Trades: {res['total_trades']}")
        print(f"  Final Balance: ${res['final_balance']:.2f}")
        print(f"  Max Drawdown: {res['max_drawdown']*100:.2f}%")
        print("-" * 40)
