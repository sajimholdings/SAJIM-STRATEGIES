import MetaTrader5 as mt5
import time

print("Initializing MT5...")
res = mt5.initialize(path=r"C:\Program Files\MetaTrader 5\terminal64.exe")
print("Initialized:", res)
if not res:
    print("Error:", mt5.last_error())
else:
    term = mt5.terminal_info()
    acc = mt5.account_info()
    print(f"Terminal Connected: {term.connected}")
    print(f"Trade Allowed: {term.trade_allowed}")
    print(f"Account: {acc.login if acc else 'None'} | Server: {acc.server if acc else 'None'}")
    if acc:
        print(f"Balance: ${acc.balance:,.2f} | Equity: ${acc.equity:,.2f}")
    
    # Check open charts
    print("\nCharts currently open:")
    for ch in [mt5.chart_first()] if mt5.chart_first() > 0 else []:
        print(f"Chart ID: {ch}, Symbol: {mt5.chart_symbol(ch)}")
