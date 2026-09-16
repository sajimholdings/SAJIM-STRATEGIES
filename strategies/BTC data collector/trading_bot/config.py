# config.py

# ─── Execution Engine ────────────────────────────────────────────
# MetaTrader5 via Headway broker
# PAPER_MODE = True  → connect to DEMO account (safe for testing)
# PAPER_MODE = False → connect to LIVE account
PAPER_MODE = True

MT5_LOGIN    = 4696497        # Headway Demo account (auto-detected)
MT5_PASSWORD = ""             # Not required -- terminal already logged in
MT5_SERVER   = "Headway-Demo" # Auto-detected from running terminal

# Optional: path to the MT5 terminal executable if auto-detection fails
# MT5_PATH = r"C:\Program Files\Headway Group\MetaTrader 5\terminal64.exe"
MT5_PATH     = None

# Trade tagging — helps identify bot orders in MT5 trade history
MT5_MAGIC_BTC  = 20001    # Magic number for BTC Fade bot orders
MT5_MAGIC_ETH  = 20002    # Magic number for ETH Momentum bot orders
MT5_DEVIATION  = 20       # Max slippage in points (20 = 2 pips)

# ─── Telegram ────────────────────────────────────────────────────
# Deferred to next phase — leave blank
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID   = ""

# ─── Risk (shared) ──────────────────────────────────────────────
RISK_PER_TRADE   = 0.01   # 1% of account equity per trade
MAX_POSITION_PCT = 0.30   # Never risk > 30% of account in one position
FEE_PCT          = 0.0    # MT5 CFD spread is baked into price; set to 0 for now
                           # Adjust if broker charges explicit commission

# ─── BTC Fade Strategy ──────────────────────────────────────────
BTC_SYMBOL          = "BTCUSD"   # Headway CFD symbol — verify in MT5 Market Watch
BTC_TIMEFRAME       = "1h"
BTC_ATR_PERIOD      = 14
BTC_ATR_MA_PERIOD   = 50
BTC_VOL_THRESHOLD   = 1.0        # ATR must be > 1.0x its 50-period SMA
BTC_BODY_ATR_ENTRY  = 1.75       # Candle body must exceed 1.75x ATR
BTC_EMA_PERIOD      = 14
BTC_TP_MEAN_FRACTION = 0.7       # TP at 70% of distance back to EMA
BTC_SL_ATR_MULTIPLE = 1.5        # SL at 1.5x ATR beyond candle extreme
BTC_MAX_BARS        = 12         # Force close after 12 hours

# ─── ETH Momentum Strategy ──────────────────────────────────────
ETH_SYMBOL           = "ETHUSD"  # Headway CFD symbol — verify in MT5 Market Watch
ETH_TIMEFRAME        = "1h"
ETH_ATR_PERIOD       = 14
ETH_ATR_MA_PERIOD    = 50
ETH_VOL_THRESHOLD    = 1.0
ETH_BODY_ATR_ENTRY   = 2.0       # Candle body must exceed 2.0x ATR
ETH_TRAIL_ATR_MULTIPLE = 4.0     # Trailing stop at 4x ATR from peak/trough
ETH_MAX_BARS         = 48        # Longer hold for trend trades
ETH_PARTIAL_TP_ATR   = 2.0       # Take partial at 2x ATR profit
ETH_PARTIAL_PCT      = 0.4       # Close 40% at partial TP
