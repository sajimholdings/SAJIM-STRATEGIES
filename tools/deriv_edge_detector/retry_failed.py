import asyncio
import os
import sys
from collect_data import collect_symbol, SYMBOLS, GRANULARITIES

FAILED = [
    ("1HZ75V", "1H"),
    ("BOOM500", "5M"),
    ("CRASH1000", "1H"),
    ("CRASH1000", "5M"),
    ("R_100", "1H")
]

async def main():
    print("╔══════════════════════════════════════════════════════════╗")
    print("║    Retrying failed downloads                             ║")
    print("╚══════════════════════════════════════════════════════════╝")
    
    for symbol, gran_label in FAILED:
        display_name = SYMBOLS[symbol]
        gran_secs = GRANULARITIES[gran_label]
        print(f"\nRetrying: {display_name} ({symbol}) — {gran_label}")
        
        # Retry up to 3 times
        for attempt in range(1, 4):
            try:
                count = await collect_symbol(symbol, display_name, gran_label, gran_secs)
                if count > 0:
                    print(f"  ✓ Success on attempt {attempt}!")
                    break
            except Exception as e:
                print(f"  ✗ Attempt {attempt} failed: {e}")
            await asyncio.sleep(2)
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
