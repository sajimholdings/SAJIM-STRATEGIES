import time
import MetaTrader5 as mt5
import pandas as pd
from core.mt5_connector import MT5Connector
from core.trade_executor import TradeExecutor
from strategies.gold_pullback_ai import GoldPullbackAI
from dotenv import load_dotenv
import os

def main():
    # Explicitly load from parent directory where user's .env lives
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    load_dotenv(env_path)
    print("Starting Sovereign Sniper Engine v2.0...")
    
    mt5_conn = MT5Connector()
    if not mt5_conn.initialize():
        print("Failed to initialize MT5")
        return
        
    executor = TradeExecutor(mt5_conn)
    strategy = GoldPullbackAI()
    strategy.initialize({"timeframe": "H1"})
    
    print("Engine initialized. Running execution loop...")
    
    try:
        while True:
            # For H1, we should ideally wait for candle close. We simulate polling here.
            rates = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H1, 0, 50)
            if rates is not None and len(rates) > 0:
                df = pd.DataFrame(rates)
                df['time'] = pd.to_datetime(df['time'], unit='s')
                
                market_data = strategy.analyze("XAUUSD", df)
                
                # Execute cycle
                verdict = executor.process_market_cycle("XAUUSD", market_data)
                
                # Sync phase back to local strategy tracker
                if "target_phase" in verdict:
                    strategy.current_phase = verdict["target_phase"]
                    
            time.sleep(60) # Poll every 60 seconds
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        mt5_conn.shutdown()

if __name__ == "__main__":
    main()
