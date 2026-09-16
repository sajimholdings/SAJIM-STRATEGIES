# BEEP — Behavioral Encoding & Existence Prediction

## Phase 0: The Filter Prototype

**What this does:** Takes raw forex price data, identifies which moves are real vs noise, and outputs a cleaned signal you can actually trade.

### Quick Start
```bash
pip install -r requirements.txt
python src/main.py
```

### Project Structure
```
BEEP/
├── data/              # Raw and processed forex data
├── src/
│   ├── data_loader.py # Fetches historical forex data
│   ├── beep_filter.py # The core BEEP anomaly detection algorithm
│   ├── visualizer.py  # Plots raw vs cleaned price action
│   ├── backtest.py    # Tests if cleaned signals improve trading
│   └── main.py        # Entry point — runs the full pipeline
├── output/            # Charts, results, reports
├── notebooks/         # Jupyter notebooks for exploration
└── requirements.txt   # Python dependencies
```

### The Pipeline
```
Raw Forex Data → BEEP Filter → Cleaned Data → Backtest → Results
```

© 2026 Visyx — BEEP Project. Proprietary.
