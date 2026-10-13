"""Smoke test v3: uses correct INSTRUMENT_* constants via dukascopy_python.fetch()"""
import sys
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
import logging
logging.getLogger("DUKASCRIPT").setLevel(logging.ERROR)

from datetime import datetime
import dukascopy_python as duka
import dukascopy_python.instruments as instruments

print("Smoke test — fetching EURUSD H1 for 3 days (Jan 6-9 2025)...")

try:
    df = duka.fetch(
        instrument=instruments.INSTRUMENT_FX_MAJORS_EUR_USD,
        interval=duka.INTERVAL_HOUR_1,
        offer_side=duka.OFFER_SIDE_BID,
        start=datetime(2025, 1, 6),
        end=datetime(2025, 1, 9),
        max_retries=3,
    )
    if df is not None and len(df) > 0:
        print(f"SUCCESS: {len(df)} rows")
        print(f"Columns: {list(df.columns)}")
        print(f"First row:\n{df.head(1)}")
        print(f"Last row:\n{df.tail(1)}")
    else:
        print(f"WARNING: empty DataFrame returned (len={len(df) if df is not None else 'None'})")
except Exception as e:
    import traceback
    print(f"FAILED: {e}")
    traceback.print_exc()
