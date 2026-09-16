# run_eth.py
import os
import sys
import logging
import traceback

# ── Ensure directories exist before logging setup ────────────────────────────
os.makedirs("logs",  exist_ok=True)
os.makedirs("state", exist_ok=True)

import config
import exchange
import notifications
import bot_eth_momentum

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/eth.log"),
        logging.StreamHandler(sys.stdout),
    ]
)


def main():
    import time
    start = time.time()
    logging.info("=" * 60)
    logging.info("ETH Momentum Bot -- run started")
    logging.info(f"Mode: {'DEMO' if config.PAPER_MODE else 'LIVE'}")

    # ── Connect to MT5 ───────────────────────────────────────────────────────
    try:
        if not exchange.initialize():
            logging.error("Failed to connect to MT5 terminal. Aborting.")
            notifications.send_telegram(
                "WARNING *ETH Bot*: Failed to connect to MT5 terminal.",
                config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID
            )
            return
    except Exception as e:
        logging.error(f"MT5 initialization error: {e}")
        return

    # ── Run bot logic ────────────────────────────────────────────────────────
    try:
        bot_eth_momentum.run()
        elapsed = time.time() - start
        logging.info(f"ETH Momentum Bot -- run completed in {elapsed:.1f}s")
    except Exception as e:
        err_msg = traceback.format_exc()
        logging.error(f"FATAL BOT ERROR:\n{err_msg}")
        notifications.send_telegram(
            f"WARNING *ETH Bot Error*\n```\n{str(e)[:500]}\n```",
            config.TELEGRAM_BOT_TOKEN,
            config.TELEGRAM_CHAT_ID,
        )
    finally:
        # ── Always disconnect MT5 cleanly ─────────────────────────────────────
        exchange.shutdown()

    logging.info("=" * 60)


if __name__ == "__main__":
    main()
