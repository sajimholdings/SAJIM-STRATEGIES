import MetaTrader5 as mt5
from datetime import datetime, timedelta
import pandas as pd

def get_history():
    if not mt5.initialize():
        print("initialize() failed")
        mt5.shutdown()
        return

    # Get history for today
    today = datetime.now()
    from_date = today - timedelta(days=1)
    to_date = today + timedelta(days=1)

    deals = mt5.history_deals_get(from_date, to_date)
    if deals == None or len(deals) == 0:
        print("No deals found.")
        mt5.shutdown()
        return

    df = pd.DataFrame(list(deals), columns=deals[0]._asdict().keys())
    df['time'] = pd.to_datetime(df['time'], unit='s')
    
    # Filter only relevant columns for summary
    print(df[['time', 'symbol', 'type', 'volume', 'price', 'profit', 'entry']].to_string())

    total_profit = df['profit'].sum()
    print(f"\nTotal Profit today: {total_profit}")

    mt5.shutdown()

if __name__ == "__main__":
    get_history()
