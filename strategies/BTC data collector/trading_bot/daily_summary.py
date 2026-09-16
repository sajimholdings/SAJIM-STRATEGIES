# daily_summary.py
"""
Daily summary script — run once per day at 00:05 UTC via cron:
  5 0 * * * cd /path/to/trading_bot && /usr/bin/python3 daily_summary.py >> logs/summary.log 2>&1
"""
import os
import sys
import datetime
import logging

os.makedirs("logs",  exist_ok=True)
os.makedirs("state", exist_ok=True)

import config
import state
import notifications

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/summary.log"),
        logging.StreamHandler(sys.stdout),
    ]
)


def count_today_trades(trade_history: list) -> int:
    """Count trades closed today (UTC)."""
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    return sum(1 for t in trade_history if t.get("exit_time", "").startswith(today))


def format_status(open_trade: dict | None) -> str:
    if not open_trade:
        return "No open trade"
    return (
        f"{open_trade['direction']} from ${open_trade['entry_price']:,.2f} "
        f"({open_trade['bars_elapsed']}h)"
    )


def format_open_pnl(open_trade: dict | None) -> str:
    """
    We don't have current price here, so show entry info only.
    The 6-hour Telegram updates from the bot show live open PnL.
    """
    if not open_trade:
        return ""
    return f"\n  Entry ATR: {open_trade.get('entry_atr', 0):.2f}"


def main():
    try:
        logging.info("Generating daily summary...")

        btc_st = state.load_state("state/btc_state.json")
        eth_st = state.load_state("state/eth_state.json")

        today    = datetime.datetime.utcnow().strftime("%B %d, %Y")
        initial  = config.PAPER_STARTING_BALANCE

        # BTC stats
        btc_open   = btc_st.get("open_trade")
        btc_trades = btc_st.get("total_trades", 0)
        btc_pnl    = btc_st.get("total_pnl", 0.0)
        btc_pct    = btc_pnl / initial * 100 if initial > 0 else 0.0
        btc_today  = count_today_trades(btc_st.get("trade_history", []))

        # ETH stats
        eth_open   = eth_st.get("open_trade")
        eth_trades = eth_st.get("total_trades", 0)
        eth_pnl    = eth_st.get("total_pnl", 0.0)
        eth_pct    = eth_pnl / initial * 100 if initial > 0 else 0.0
        eth_today  = count_today_trades(eth_st.get("trade_history", []))

        # Combined
        comb_balance = (
            btc_st.get("balance", initial) if config.PAPER_MODE
            else btc_st.get("balance", initial)
        )
        comb_pnl = btc_pnl + eth_pnl
        comb_pct = comb_pnl / (2 * initial) * 100 if initial > 0 else 0.0

        # ETH partial note
        eth_status = format_status(eth_open)
        if eth_open and eth_open.get("partial_filled"):
            eth_status += " (partial TP hit)"

        msg = (
            f"📈 *Daily Summary — {today}*\n\n"
            f"*BTC Fade:*\n"
            f"  Status: {format_status(btc_open)}\n"
            f"  Today: {btc_today} trades\n"
            f"  Total: {btc_trades} trades | {btc_pnl:+.2f} USD | {btc_pct:+.1f}%\n\n"
            f"*ETH Momentum:*\n"
            f"  Status: {eth_status}\n"
            f"  Today: {eth_today} trades\n"
            f"  Total: {eth_trades} trades | {eth_pnl:+.2f} USD | {eth_pct:+.1f}%\n\n"
            f"Combined PnL: {comb_pnl:+.2f} USD ({comb_pct:+.1f}%)\n"
            f"Paper Balance: ${comb_balance:,.2f}"
        )

        notifications.send_telegram(msg, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
        logging.info("Daily summary sent successfully.")
        # Always print to stdout for cron log
        print(msg)

    except Exception as e:
        logging.error(f"Daily summary error: {e}", exc_info=True)
        notifications.send_telegram(
            f"⚠️ *Daily Summary Failed*\n```\n{str(e)[:300]}\n```",
            config.TELEGRAM_BOT_TOKEN,
            config.TELEGRAM_CHAT_ID,
        )


if __name__ == "__main__":
    main()
