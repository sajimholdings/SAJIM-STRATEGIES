"""Debug v3: test last_update=0, session cookies, and Dukascopy bi5 CDN endpoint"""
import sys, requests, json, random, string, struct, bz2
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
from datetime import datetime, timezone

def rand_jsonp():
    return "_callbacks____" + "".join(random.choices(string.ascii_letters + string.digits, k=9))

BASE_URL = "https://freeserv.dukascopy.com/2.0/index.php"
HEADERS_FULL = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0",
    "Host": "freeserv.dukascopy.com",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://freeserv.dukascopy.com/2.0/?path=chart/index&instrument=EUR/USD&period=5&offerSide=BID",
}

# Test 1: last_update = 0
print("=== Test 1: last_update=0 ===")
jsonp = rand_jsonp()
params = {"path":"chart/json3","splits":"true","stocks":"true","time_direction":"N",
          "jsonp":jsonp,"last_update":"0","offer_side":"B","instrument":"EURUSD","interval":"5MIN","limit":"10"}
resp = requests.get(BASE_URL, headers=HEADERS_FULL, params=params, timeout=15)
print(f"Status: {resp.status_code}, Response (300 chars): {resp.text[:300]}")

# Test 2: Use session (get cookies first)
print("\n=== Test 2: with session/cookies ===")
session = requests.Session()
session.headers.update(HEADERS_FULL)
# First hit the main page to get cookies
try:
    session.get("https://freeserv.dukascopy.com/2.0/", timeout=10)
    print(f"Session cookies: {dict(session.cookies)}")
except Exception as e:
    print(f"Session init error: {e}")

jsonp = rand_jsonp()
params = {"path":"chart/json3","splits":"true","stocks":"true","time_direction":"N",
          "jsonp":jsonp,"last_update":"0","offer_side":"B","instrument":"EURUSD","interval":"5MIN","limit":"10"}
resp = session.get(BASE_URL, params=params, timeout=15)
print(f"Status: {resp.status_code}, Response: {resp.text[:300]}")

# Test 3: Dukascopy datafeed CDN bi5 format (different endpoint - no auth needed)
# Note: month is 0-indexed in dukascopy CDN!
# URL: https://datafeed.dukascopy.com/datafeed/EURUSD/2025/00/06/BID_candles_min_1.bi5
print("\n=== Test 3: Dukascopy datafeed CDN (bi5) ===")
# Jan 2025 = month index 00
cdn_url = "https://datafeed.dukascopy.com/datafeed/EURUSD/2025/00/06/BID_candles_min_1.bi5"
try:
    r = requests.get(cdn_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    print(f"CDN Status: {r.status_code}, Content-Length: {len(r.content)} bytes")
    if r.status_code == 200 and len(r.content) > 0:
        # Try to decompress bi5 (it's BZ2)
        try:
            data = bz2.decompress(r.content)
            print(f"Decompressed: {len(data)} bytes")
            # M1 OHLCV: each record = 5 x int32 = 20 bytes
            # timestamp(ms from hour start), open, high, low, close, volume(float)
            # Actually: 4 bytes each for timestamp, open, high, low, close = 20 bytes + volume?
            record_size = 20  # 5 x 4 bytes
            n_records = len(data) // record_size
            print(f"Records: {n_records}")
            if n_records > 0:
                # Parse first record
                rec = struct.unpack(">iiiii", data[:20])
                print(f"First record (raw): {rec}")
                # timestamp is ms from hour start, prices need dividing by 100000 for forex
        except Exception as e:
            print(f"Decompress error: {e}")
    else:
        print(f"CDN failed or empty")
except Exception as e:
    print(f"CDN Error: {e}")
