# cron_simulator.py
import time
import os
import subprocess
import datetime
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def run_script(script_name: str):
    logging.info(f"--- Running {script_name} ---")
    # For windows compatibility, use sys.executable
    import sys
    subprocess.run([sys.executable, script_name])

def main():
    logging.info("Starting cron simulator...")
    logging.info("This script will run bot_btc.py and bot_eth.py every minute instead of every hour for testing.")
    
    while True:
        now = datetime.datetime.now()
        logging.info(f"Tick: {now.strftime('%H:%M:%S')}")
        
        # In this simulator, we just run both scripts immediately every minute
        run_script("run_btc.py")
        run_script("run_eth.py")
        
        # Sleep for 60 seconds
        time.sleep(60)

if __name__ == "__main__":
    main()
