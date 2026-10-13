# SAJIM HOLDINGS — SANDBOX COPY
### Safe Testing Environment — DO NOT deploy from here

**Copied from:** `C:\Users\sajim\OneDrive\SAJIM HOLDINGS`  
**Copied on:** 2026-09-15  
**Purpose:** Validate BEEP engine against Dukascopy historical parquet data before touching the live system.

---

## Rules of Engagement

| Rule | Detail |
|---|---|
| 🟢 **Safe to modify** | Everything in this folder |
| 🔴 **Never modify from here** | `C:\Users\sajim\OneDrive\SAJIM HOLDINGS` (the live system) |
| ✅ **Goal** | Validate BEEP signals on real Dukascopy data, find discrepancies |
| 📋 **After validation** | Only then push verified changes to live system |

---

## What Was Excluded from Copy
- `broadcast_stream.jsonl` (33MB Telegram log — irrelevant for backtesting)
- `overnight_execution.log` (24MB — irrelevant)
- `COMPLETE_CONVERSATION_HISTORY.md` (2MB chat log)
- `Meta AI.zip` (archive)
- `__pycache__/` (all compiled bytecode)

---

## Data Available for Testing
All Dukascopy parquet files are at `F:\Tete\SAJIM STRATEGIES\data\parquet\`

**20 symbols × 6 timeframes = 120 parquet files:**

Symbols: AUDUSD, BTCUSD, ETHUSD, EURGBP, EURJPY, EURUSD, GBPJPY, GBPUSD,  
GER40, LTCUSD, NZDUSD, UK100, US30, US500, USDCAD, USDCHF, USDJPY,  
XAGUSD, XAUUSD, XRPUSD

Timeframes: M1, M5, M15, M30, H1, H4

---

## Key Findings from Live Edge Matrix (Sept 9, 2026 — 204 fronts)

Top edges:
1. USDCAD M15 — PF: 1.99, WR: 53.5%, E: +0.372
2. EURJPY M5  — PF: 1.87, WR: 56.2%, E: +0.334
3. NZDJPY H1  — PF: 1.84, WR: 54.9%, E: +0.284

Toxic bleeders — **Universal Rule:**  
> Reject all M1 setups. Reject any asset where spread > 12% of ATR.

---

## Testing Plan

1. **[TEST-001]** Run BEEP engine on USDCAD M15 parquet → compare signal stats to live edge matrix
2. **[TEST-002]** Run across all 6 EURJPY timeframes → validate WR and PF match
3. **[TEST-003]** Run YoungSurge / ExhaustionReversion cartridges on Dukascopy data
4. **[TEST-004]** Monte Carlo on top 5 edges → validate drawdown assumptions
5. **[TEST-005]** Identify mismatches → these are the things to fix in live system

---

## Sandbox Scripts (add yours here)
- `tests/test_beep_on_dukascopy.py` — Run BEEP engine against parquet files
