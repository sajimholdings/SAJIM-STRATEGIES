# hourly_summary.py
import datetime
import logging
import config
import state
import notifications

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/summary.log"),
        logging.StreamHandler(),
    ]
)

def format_bot_summary(name: str, st: dict) -> str:
    open_trade = st.get("open_trade")
    total_trades = st.get("total_trades", 0)
    total_pnl = st.get("total_pnl", 0.0)
    
    # Calculate initial balance to show % gain
    initial_balance = config.PAPER_STARTING_BALANCE if config.PAPER_MODE else 0 # Real mode needs complex calc, simplified here
    
    # This is a bit simplified for live mode
    pct = (total_pnl / initial_balance * 100) if initial_balance > 0 else 0
    
    if not open_trade:
        status = "No open trade"
        open_pnl_str = ""
    else:
        status = f"{open_trade['direction']} from ${open_trade['entry_price']:.2f} ({open_trade['bars_elapsed']}h)"
        # Simple open pnl (not accurate to current price, just entry vs mark if we had it, omitting for simplicity in summary)
        open_pnl_str = f"\n  Open PnL: See bot updates"
        
    return f"{name}:\n  Status: {status}{open_pnl_str}\n  Total: {total_trades} trades | {total_pnl:+.2f} USD | {pct:+.1f}%\n"

def main():
    try:
        logging.info("Generating hourly summary...")
        btc_state = state.load_state("state/btc_state.json", True)
        eth_state = state.load_state("state/eth_state.json", False)
        
        today = datetime.datetime.utcnow().strftime("%B %d, %Y")
        
        btc_text = format_bot_summary("BTC Fade", btc_state)
        eth_text = format_bot_summary("ETH Momentum", eth_state)
        
        comb_pnl = btc_state.get("total_pnl", 0) + eth_state.get("total_pnl", 0)
        
        msg = f"📈 *Hourly Summary — {today}*\n\n{btc_text}\n{eth_text}\nCombined PnL: {comb_pnl:+.2f} USD"
        
        notifications.send_telegram(msg, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
        logging.info("Hourly summary saved.")
    except Exception as e:
        logging.error(f"Summary error: {e}")

if __name__ == "__main__":
    main()
