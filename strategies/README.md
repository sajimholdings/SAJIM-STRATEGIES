# SAJIM Strategies Directory

This directory is the dedicated repository for all quantitative trading strategies, expert advisors, and algorithmic bots evaluated by the SAJIM platform.

## Directory Structure

```
strategies/
├── SajimTraders/                    # Master Sajim Holdings Quantitative Labs repo (BEEP v2, Prop Firm, Omniverse)
├── BEEP_MASTER_PACKAGE/             # High-velocity momentum void hunter & baseline follower
├── smc_daemon_project/              # Smart Money Concepts order blocks & liquidity sweeps
├── Scalper1/                        # MQL5 headless high-frequency micro-grid EA
├── GoldOrb/                         # Gold opening range breakout EA
├── backtrader-pullback-window-xauusd/ # SunriseOgle London/NY session breakout
├── XAUUSD_TRADING_ASISTENT_AI/      # AI/ML volatility pullback system
├── Deriv UpDown/                    # Deriv WebSocket synthetic momentum bot
├── Deriv accumulatorsbot/           # Deriv accumulator bot collection
├── Deriv trading/                   # Deriv execution scripts
├── BTC data collector/              # Crypto tick data acquisition
└── TEMPLATE_STRATEGY/               # Starter template for adding new strategies
```

## How to Add a New Strategy

1. **Create a new folder** inside `strategies/` (or clone a git repository):
   ```bash
   git clone <repo_url> strategies/<MyStrategyName>
   ```
2. **Configure the manifest**:
   Copy or customize `sajim_strategy.yaml` inside your strategy folder.
3. **Trigger Automatic Discovery**:
   Run the scanner to immediately benchmark and rank your strategy:
   ```powershell
python -m sajim_quant.core.discovery
   ```
4. View results on the live dashboard at `http://localhost:3000`.
