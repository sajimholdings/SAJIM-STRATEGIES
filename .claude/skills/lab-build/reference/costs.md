# Cost model (for the spec's Mechanism section)

Copied from `sajim_quant/core/instruments.py` (pinned by `tests/lab/test_claude_layer.py`;
the server always uses `instruments.py` itself). Every trade pays, per round trip:

    cost in price units  =  entry_price x round_trip_bps / 10000  +  2 x slippage_points x point

The simulator also runs the strategy at 1.5x and 2x this cost (gate G6), so an idea
whose average winner is not several times the round-trip cost will not pass.
Example: XAUUSD at 2600 costs 2600 x 3 / 10000 + 0.1 = 0.88 per round trip (8.8 pips of
0.1); EURUSD at 1.10 costs 1.10 x 1.2 / 10000 + 0.0001 = 0.000232 (2.3 pips).

| Symbol | Class | Quote ccy | point | pip | round_trip_bps | slippage_points (per side) | 2 x slippage in price |
|---|---|---|---|---|---|---|---|
| EURUSD | FX | USD | 1e-05 | 0.0001 | 1.2 | 5 | 0.0001 |
| GBPUSD | FX | USD | 1e-05 | 0.0001 | 1.2 | 5 | 0.0001 |
| AUDUSD | FX | USD | 1e-05 | 0.0001 | 1.2 | 5 | 0.0001 |
| NZDUSD | FX | USD | 1e-05 | 0.0001 | 1.2 | 5 | 0.0001 |
| USDCAD | FX | CAD | 1e-05 | 0.0001 | 1.2 | 5 | 0.0001 |
| USDCHF | FX | CHF | 1e-05 | 0.0001 | 1.2 | 5 | 0.0001 |
| EURGBP | FX | GBP | 1e-05 | 0.0001 | 1.2 | 5 | 0.0001 |
| USDJPY | FX | JPY | 0.001 | 0.01 | 1.2 | 5 | 0.01 |
| GBPJPY | FX | JPY | 0.001 | 0.01 | 1.2 | 5 | 0.01 |
| EURJPY | FX | JPY | 0.001 | 0.01 | 1.2 | 5 | 0.01 |
| XAUUSD | METAL | USD | 0.01 | 0.1 | 3 | 5 | 0.1 |
| XAGUSD | METAL | USD | 0.001 | 0.01 | 4 | 10 | 0.02 |
| BTCUSD | CRYPTO | USD | 0.01 | 1 | 10 | 200 | 4 |
| ETHUSD | CRYPTO | USD | 0.01 | 0.1 | 10 | 50 | 1 |
| LTCUSD | CRYPTO | USD | 0.01 | 0.1 | 12 | 10 | 0.2 |
| XRPUSD | CRYPTO | USD | 1e-05 | 0.0001 | 12 | 20 | 0.0004 |
| US30 | INDEX | USD | 0.1 | 1 | 2 | 20 | 4 |
| US500 | INDEX | USD | 0.01 | 0.1 | 2 | 30 | 0.6 |
| GER40 | INDEX | EUR | 0.1 | 1 | 2 | 20 | 4 |
| UK100 | INDEX | GBP | 0.1 | 1 | 2 | 20 | 4 |

Refused combinations (coverage below 90%): BTCUSD M5/M15/M30, ETHUSD M5/M15/M30, LTCUSD M5.
Breadth symbols must share the primary's class (FX with FX, METAL with METAL, ...).
