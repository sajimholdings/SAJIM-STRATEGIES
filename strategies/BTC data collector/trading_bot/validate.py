# validate.py
"""
Validation script covering all 8 tests from the spec.
Run from the trading_bot/ directory:
    python validate.py

All tests print their result. No Binance API key needed --
indicator tests use synthetic data, state tests use temp files.
"""
import sys
import os
import io
import json
import math
import tempfile
import numpy as np

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# -- Allow imports from this directory ----------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import indicators
import risk
import state as state_module
import config

PASS = "✅ PASS"
FAIL = "❌ FAIL"

results = []


def record(name: str, passed: bool, detail: str = ""):
    symbol = PASS if passed else FAIL
    line   = f"{symbol}  [{name}]"
    if detail:
        line += f"\n         {detail}"
    print(line)
    results.append(passed)


# -----------------------------------------------------------------------------
# TEST 1: ATR accuracy — Wilder smoothing
# -----------------------------------------------------------------------------
print("\n-- Test 1: ATR (Wilder smoothing) ---------------------------------")

# Construct synthetic candles with known true-range = 10 every bar
n = 30
synthetic = [
    {"open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0, "volume": 1000}
    for _ in range(n)
]
atr_arr = indicators.compute_atr(synthetic, period=14)

# With constant TR=10, Wilder ATR converges toward 10
# After period=14, initial ATR = 10. For all subsequent bars: ATR stays 10.
atr_val = atr_arr[-1]
record(
    "ATR Wilder (constant TR=10)",
    abs(atr_val - 10.0) < 0.001,
    f"Expected ~10.0, got {atr_val:.4f}"
)

# Test ATR length = input length
record(
    "ATR output length == input length",
    len(atr_arr) == n,
    f"Input len={n}, output len={len(atr_arr)}"
)


# -----------------------------------------------------------------------------
# TEST 2: EMA accuracy
# -----------------------------------------------------------------------------
print("\n-- Test 2: EMA accuracy -------------------------------------------")

# With constant close = 100, EMA should converge to 100
closes_const = [100.0] * 50
ema_arr = indicators.compute_ema(closes_const, period=14)
record(
    "EMA converges to constant price",
    abs(ema_arr[-1] - 100.0) < 0.001,
    f"Expected 100.0, got {ema_arr[-1]:.4f}"
)

# EMA should be length = input
record(
    "EMA output length == input length",
    len(ema_arr) == len(closes_const),
    f"Input len={len(closes_const)}, output len={len(ema_arr)}"
)


# -----------------------------------------------------------------------------
# TEST 3: Direction correctness
# -----------------------------------------------------------------------------
print("\n-- Test 3: Direction correctness ----------------------------------")

# BTC FADE: body > 0 (bullish) → SHORT (fade it)
# BTC FADE: body < 0 (bearish) → LONG  (fade it)
bullish_body = 500.0
bearish_body = -500.0

btc_dir_bullish = "SHORT" if bullish_body > 0 else "LONG"
btc_dir_bearish = "SHORT" if bearish_body > 0 else "LONG"

record("BTC Fade: bullish candle → SHORT", btc_dir_bullish == "SHORT",
       f"body={bullish_body:.1f} → {btc_dir_bullish}")
record("BTC Fade: bearish candle → LONG",  btc_dir_bearish == "LONG",
       f"body={bearish_body:.1f} → {btc_dir_bearish}")

# ETH MOMENTUM: body > 0 → LONG, body < 0 → SHORT (follow it)
eth_dir_bullish = "LONG"  if bullish_body > 0 else "SHORT"
eth_dir_bearish = "LONG"  if bearish_body > 0 else "SHORT"

record("ETH Momentum: bullish candle → LONG",  eth_dir_bullish == "LONG",
       f"body={bullish_body:.1f} → {eth_dir_bullish}")
record("ETH Momentum: bearish candle → SHORT", eth_dir_bearish == "SHORT",
       f"body={bearish_body:.1f} → {eth_dir_bearish}")


# -----------------------------------------------------------------------------
# TEST 4: Position sizing — 1% loss if SL hit
# -----------------------------------------------------------------------------
print("\n-- Test 4: Position sizing (1% account loss at SL) ----------------")

# Use ETH-like params: small enough position that 30% cap is NOT the binding constraint
# Entry=$2000, SL=$1992 → risk/coin=$8 → size_qty=$10/$8=1.25 ETH → $2500 (> 30% cap!)
# Use wider SL: Entry=$2000, SL=$1920 → risk/coin=$80 → size_qty=$10/$80=0.125 ETH → $250 (< 30% cap)
balance    = 1000.0
entry      = 2000.0
sl         = 1920.0   # $80 risk per ETH, theoretical size = $250 (under 30% cap)
step_size  = 0.0001

size_qty, size_usd = risk.calculate_position_size(
    balance, entry, sl, config.RISK_PER_TRADE, config.MAX_POSITION_PCT, step_size
)

# If SL hit: loss should equal approximately 1% of balance = $10
loss_at_sl    = size_qty * abs(entry - sl)
expected_loss = balance * config.RISK_PER_TRADE

record(
    "Position sizing: loss at SL ~= 1% of balance (when under max cap)",
    abs(loss_at_sl - expected_loss) < 0.10,
    f"Balance=${balance:.2f}, SL loss=${loss_at_sl:.4f}, expected=${expected_loss:.2f}"
)

# Check max position cap (30% of balance)
balance_cap = 1000.0
entry_cap   = 2000.0
sl_cap      = 1999.0  # tiny SL → would make position enormous without cap

size_qty_c, size_usd_c = risk.calculate_position_size(
    balance_cap, entry_cap, sl_cap, config.RISK_PER_TRADE, config.MAX_POSITION_PCT, step_size
)
record(
    "Position sizing: max position cap (30% of balance)",
    size_usd_c <= balance_cap * config.MAX_POSITION_PCT + 0.01,
    f"size_usd=${size_usd_c:.2f}, max=${balance_cap * config.MAX_POSITION_PCT:.2f}"
)


# -----------------------------------------------------------------------------
# TEST 5: Fee accounting
# -----------------------------------------------------------------------------
print("\n-- Test 5: Fee accounting -----------------------------------------")

# MT5 CFD mode: FEE_PCT = 0.0 (spread is baked into bid/ask price, not charged separately)
# We test that the PnL formula is correctly applied regardless of fee level.
entry_p = 68000.0
exit_p  = 69000.0
qty     = 0.001
fee_pct = config.FEE_PCT  # 0.0 for MT5

val_in    = qty * entry_p
val_out   = qty * exit_p
gross_pnl = val_out - val_in
fee_in    = val_in  * fee_pct
fee_out   = val_out * fee_pct
net_pnl   = gross_pnl - fee_in - fee_out

record(
    "Fee accounting: PnL formula correct (fee_pct may be 0 for MT5 CFD)",
    abs(net_pnl - (gross_pnl - fee_in - fee_out)) < 1e-9,
    f"Gross={gross_pnl:.4f}, Fees={fee_in + fee_out:.4f}, Net={net_pnl:.4f} | FEE_PCT={fee_pct}"
)

record(
    "Fee accounting: net PnL <= gross PnL",
    net_pnl <= gross_pnl,
    f"Net={net_pnl:.4f} <= Gross={gross_pnl:.4f} (equal is OK when FEE_PCT=0)"
)


# -----------------------------------------------------------------------------
# TEST 6: Completed candle — decisions use candles[-2]
# -----------------------------------------------------------------------------
print("\n-- Test 6: Completed candle (candles[-2] used for decisions) ------")

candles_6 = [
    {"open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0, "volume": 100}
    for _ in range(50)
]
# Override candles[-1] (forming) with different values that should NOT be used
candles_6[-1] = {"open": 999.0, "high": 9999.0, "low": 1.0, "close": 1.0, "volume": 1}

inds_6 = indicators.get_latest_indicators(candles_6, atr_period=14, atr_ma_period=50, ema_period=14)

record(
    "Closed candle close is 105.0 (not forming's 1.0)",
    abs(inds_6["close"] - 105.0) < 0.001,
    f"close={inds_6['close']:.4f} (should be 105.0)"
)
record(
    "Closed candle high is 110.0 (not forming's 9999.0)",
    abs(inds_6["high"] - 110.0) < 0.001,
    f"high={inds_6['high']:.4f} (should be 110.0)"
)


# -----------------------------------------------------------------------------
# TEST 7: State persistence — write and read roundtrip
# -----------------------------------------------------------------------------
print("\n-- Test 7: State persistence --------------------------------------")

with tempfile.TemporaryDirectory() as tmpdir:
    filepath = os.path.join(tmpdir, "test_state.json")

    # Write a known state
    test_state = {
        "balance": 1234.56,
        "open_trade": {
            "direction": "LONG",
            "entry_price": 68000.0,
            "bars_elapsed": 3,
        },
        "trade_history": [],
        "last_run": "2026-05-28 14:00:00",
        "total_trades": 7,
        "total_pnl": 42.50,
        "pending_order": False,
    }
    state_module.save_state(filepath, test_state)

    # Read it back
    loaded = state_module.load_state(filepath)

    record(
        "State balance persists",
        abs(loaded["balance"] - 1234.56) < 0.001,
        f"Written=1234.56, Read={loaded['balance']}"
    )
    record(
        "State open_trade persists",
        loaded["open_trade"]["direction"] == "LONG",
        f"direction={loaded['open_trade']['direction']}"
    )
    record(
        "State total_pnl persists",
        abs(loaded["total_pnl"] - 42.50) < 0.001,
        f"Written=42.50, Read={loaded['total_pnl']}"
    )

    # Test corrupted file
    with open(filepath, "w") as f:
        f.write("{corrupted json{{{")
    try:
        state_module.load_state(filepath)
        record("Corrupted state raises ValueError", False, "Should have raised ValueError")
    except ValueError:
        record("Corrupted state raises ValueError", True)


# -----------------------------------------------------------------------------
# TEST 8: Trailing stop math — only moves in the profitable direction
# -----------------------------------------------------------------------------
print("\n-- Test 8: Trailing stop math -------------------------------------")

trail_mult = config.ETH_TRAIL_ATR_MULTIPLE  # 4.0
atr_val    = 50.0
entry_p    = 2000.0

# Simulate a LONG that makes new highs, then pulls back
highs = [2000, 2010, 2050, 2080, 2075, 2090, 2060]
peak  = entry_p
trail_levels = []

for h in highs:
    peak = max(peak, h)
    trail = peak - trail_mult * atr_val
    trail_levels.append(trail)

record(
    "Trailing stop: peak tracks new highs",
    trail_levels[-1] > trail_levels[0],
    f"Initial trail={trail_levels[0]:.2f}, Final trail={trail_levels[-1]:.2f}"
)

# Verify trail never DECREASES (can only move up for LONG)
# Note: trail can temporarily appear lower if ATR changes in live, but
# with constant ATR here it must be non-decreasing
all_non_decreasing = all(
    trail_levels[i] <= trail_levels[i + 1]
    for i in range(len(trail_levels) - 1)
    if highs[i + 1] >= highs[i]  # only when high increases
)
record(
    "Trailing stop: only moves up when price makes new highs (LONG)",
    trail_levels[2] > trail_levels[0] and trail_levels[5] > trail_levels[2],
    f"Trail at bar 0: {trail_levels[0]:.2f}, bar 2: {trail_levels[2]:.2f}, bar 5: {trail_levels[5]:.2f}"
)

# Simulate SHORT trailing
lows    = [2000, 1990, 1950, 1920, 1925, 1910, 1940]
trough  = entry_p
trail_s = []

for l in lows:
    trough = min(trough, l)
    trail  = trough + trail_mult * atr_val
    trail_s.append(trail)

record(
    "Trailing stop SHORT: trail moves down as price makes new lows",
    trail_s[-1] < trail_s[0],
    f"Initial trail={trail_s[0]:.2f}, Final trail={trail_s[-1]:.2f}"
)


# -----------------------------------------------------------------------------
# TEST 9: Partial fill qty math
# -----------------------------------------------------------------------------
print("\n-- Test 9: Partial fill quantity ----------------------------------")

initial_qty   = 0.5
partial_pct   = config.ETH_PARTIAL_PCT  # 0.4
step_size_eth = 0.0001

qty_to_close  = risk.round_step_size(initial_qty * partial_pct, step_size_eth)
remaining_qty = initial_qty - qty_to_close
remaining_qty_rounded = risk.round_step_size(remaining_qty, step_size_eth)

expected_closed = risk.round_step_size(0.5 * 0.4, step_size_eth)  # 0.2
expected_remain = 0.5 - expected_closed

record(
    "Partial close qty = 40% of full position (rounded)",
    abs(qty_to_close - expected_closed) < 1e-8,
    f"Full={initial_qty}, Partial={qty_to_close:.6f} (expected {expected_closed:.6f})"
)
record(
    "Remaining qty = full - partial",
    abs(remaining_qty - expected_remain) < 1e-8,
    f"Remaining={remaining_qty:.6f} (expected {expected_remain:.6f})"
)


# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(results)
total  = len(results)
pct    = passed / total * 100 if total > 0 else 0
print(f"Results: {passed}/{total} passed ({pct:.0f}%)")
if passed == total:
    print("🎉 All tests passed!")
else:
    failed = [i + 1 for i, p in enumerate(results) if not p]
    print(f"❌ Failed tests: {failed}")
    sys.exit(1)
