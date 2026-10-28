"""
SAJIM Quant — Instrument Specifications & Execution Cost Model

Single source of truth for the contract mechanics every adapter needs in order to
turn a price move into an account-currency PnL:

    pnl = (exit_price - entry_price) * direction * lots * contract_size

Before this module existed, each adapter either hardcoded its own contract size
or skipped the conversion entirely and invented a PnL figure. Keeping the specs
here means a Backtrader run, a custom Python run and an MT5 run all price the
same move the same way — which is the whole point of normalizing at the results
layer.

Costs are modelled as a round-trip charge in basis points of notional plus a
slippage allowance in points. They are deliberately conservative defaults, not
broker-exact: every run records the numbers it used in RunMetadata so a
leaderboard row always carries its own assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class InstrumentSpec:
    """Contract mechanics and default execution assumptions for one symbol."""

    symbol: str
    asset_class: str          # "FX", "METAL", "CRYPTO", "INDEX", "SYNTHETIC"
    contract_size: float      # units of the base asset per 1.0 lot
    digits: int
    point: float              # smallest price increment
    pip: float                # conventional "pip" for this class (10 points on FX majors)
    round_trip_bps: float     # commission + spread, in basis points of notional, per round trip
    slippage_points: float    # assumed adverse fill, in points, per side
    quote_currency: str = "USD"   # currency a price move is denominated in
    max_leverage: float = 30.0    # notional cap as a multiple of equity
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 100.0

    # Every method below takes `quote_rate`: the quote currency -> account
    # currency rate (1.0 for USD-quoted symbols). See core.fx; passing the
    # default on a JPY pair overstates PnL by roughly 150x.

    def notional(self, price: float, lots: float, quote_rate: float = 1.0) -> float:
        """Account-currency notional value of a position."""
        return abs(price) * abs(lots) * self.contract_size * abs(quote_rate)

    def gross_pnl(
        self, entry_price: float, exit_price: float, lots: float, is_long: bool, quote_rate: float = 1.0
    ) -> float:
        """Price move converted to account currency, before costs."""
        move = (exit_price - entry_price) if is_long else (entry_price - exit_price)
        return move * abs(lots) * self.contract_size * abs(quote_rate)

    def round_trip_cost(self, entry_price: float, lots: float, quote_rate: float = 1.0) -> float:
        """
        Commission + spread for one full round trip, plus a slippage allowance on
        both fills. Always returns a positive number (a cost, to be subtracted).
        """
        notional = self.notional(entry_price, lots, quote_rate)
        commission = notional * (self.round_trip_bps / 10_000.0)
        slippage = (
            (self.slippage_points * self.point) * 2.0
            * abs(lots) * self.contract_size * abs(quote_rate)
        )
        return abs(commission) + abs(slippage)

    def min_tradable_stop(self) -> float:
        """
        Smallest stop distance that is not inside the execution noise floor.

        A stop tighter than the round-trip slippage allowance cannot be modelled
        honestly: fixed-fractional sizing divides by it, so a 2-pip stop on a
        $10k account asks for ~8 lots (≈90x notional) and the resulting costs
        swamp the trade. Callers skip such setups instead of pricing them.
        """
        return self.slippage_points * self.point * 4.0

    def lots_for_risk(
        self,
        risk_amount: float,
        stop_distance_price: float,
        quote_rate: float = 1.0,
        entry_price: Optional[float] = None,
        equity: Optional[float] = None,
    ) -> float:
        """
        Fixed-fractional sizing: the lot size whose loss at the stop equals
        `risk_amount`.

        Pass `entry_price` and `equity` to also cap notional at `max_leverage` x
        equity — without it, a very tight stop asks for an unfinanceable position
        (a 2-pip stop risking 2% of $10k implies ~8 lots, about 90x notional).

        Returns 0.0 when the stop is unusable or tighter than
        `min_tradable_stop()`, so callers skip the setup rather than sizing off a
        divide-by-zero or booking a position no broker would carry.
        """
        if risk_amount <= 0 or self.contract_size <= 0:
            return 0.0
        if stop_distance_price <= 0 or stop_distance_price < self.min_tradable_stop():
            return 0.0

        per_lot_loss = stop_distance_price * self.contract_size * abs(quote_rate)
        if per_lot_loss <= 0:
            return 0.0

        lots = risk_amount / per_lot_loss
        if entry_price and equity:
            lots = min(lots, self._max_lots_by_leverage(entry_price, equity, quote_rate))
        return self.normalize_lots(lots)

    def _max_lots_by_leverage(self, price: float, equity: float, quote_rate: float = 1.0) -> float:
        """Largest lot size whose notional stays within max_leverage x equity."""
        per_lot_notional = abs(price) * self.contract_size * abs(quote_rate)
        if per_lot_notional <= 0 or equity <= 0 or self.max_leverage <= 0:
            return 0.0
        return (equity * self.max_leverage) / per_lot_notional

    def lots_for_notional(
        self,
        price: float,
        target_notional: float,
        quote_rate: float = 1.0,
        equity: Optional[float] = None,
    ) -> float:
        """
        Lot size whose account-currency notional is closest to `target_notional`,
        capped at `max_leverage` x `equity` when equity is supplied.
        """
        if price <= 0 or target_notional <= 0 or self.contract_size <= 0:
            return 0.0
        per_lot = abs(price) * self.contract_size * abs(quote_rate)
        if per_lot <= 0:
            return 0.0
        lots = target_notional / per_lot
        if equity:
            lots = min(lots, self._max_lots_by_leverage(price, equity, quote_rate))
        return self.normalize_lots(lots)

    def normalize_lots(self, lots: float) -> float:
        """Snap to the broker's lot step and clamp to min/max. 0.0 if below minimum."""
        if lots <= 0 or self.lot_step <= 0:
            return 0.0
        stepped = round(lots / self.lot_step) * self.lot_step
        if stepped < self.min_lot:
            return 0.0
        return round(min(stepped, self.max_lot), 2)


# ── Explicit specs for the 20 symbols in the Dukascopy cache ─────────────────
# FX majors/crosses: 100k units per lot. JPY crosses quote to 3 digits.
# Metals: 100 oz per lot. Indices: 1 unit per point. Crypto: 1 coin per lot.

def _fx(symbol: str) -> InstrumentSpec:
    """FX spec inferred from the pair's own name; the quote currency is its last 3 letters."""
    quote = symbol[3:6].upper() if len(symbol) >= 6 else "USD"
    jpy = quote == "JPY"
    point = 0.001 if jpy else 0.00001
    return InstrumentSpec(
        symbol=symbol, asset_class="FX", contract_size=100_000.0,
        digits=3 if jpy else 5, point=point, pip=point * 10,
        round_trip_bps=1.2, slippage_points=5.0, quote_currency=quote, max_leverage=30.0,
    )


_SPECS: Dict[str, InstrumentSpec] = {
    s: _fx(s)
    for s in (
        "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURGBP",
        "USDJPY", "GBPJPY", "EURJPY",
    )
}
_SPECS.update({
    "XAUUSD": InstrumentSpec("XAUUSD", "METAL", 100.0, 2, 0.01, 0.1, 3.0, 5.0, max_leverage=20.0),
    "XAGUSD": InstrumentSpec("XAGUSD", "METAL", 5_000.0, 3, 0.001, 0.01, 4.0, 10.0, max_leverage=10.0),
    "BTCUSD": InstrumentSpec("BTCUSD", "CRYPTO", 1.0, 2, 0.01, 1.0, 10.0, 200.0, max_leverage=2.0),
    "ETHUSD": InstrumentSpec("ETHUSD", "CRYPTO", 1.0, 2, 0.01, 0.1, 10.0, 50.0, max_leverage=2.0),
    "LTCUSD": InstrumentSpec("LTCUSD", "CRYPTO", 10.0, 2, 0.01, 0.1, 12.0, 10.0, max_leverage=2.0),
    "XRPUSD": InstrumentSpec("XRPUSD", "CRYPTO", 1_000.0, 5, 0.00001, 0.0001, 12.0, 20.0, max_leverage=2.0),
    "US30": InstrumentSpec("US30", "INDEX", 1.0, 1, 0.1, 1.0, 2.0, 20.0, max_leverage=20.0),
    "US500": InstrumentSpec("US500", "INDEX", 1.0, 2, 0.01, 0.1, 2.0, 30.0, max_leverage=20.0),
    "GER40": InstrumentSpec("GER40", "INDEX", 1.0, 1, 0.1, 1.0, 2.0, 20.0, quote_currency="EUR", max_leverage=20.0),
    "UK100": InstrumentSpec("UK100", "INDEX", 1.0, 1, 0.1, 1.0, 2.0, 20.0, quote_currency="GBP", max_leverage=20.0),
})

# Deriv synthetics settle as a fixed payout on a stake, so notional mechanics do
# not apply; the importers carry their own payout model.
_SYNTHETIC_PREFIXES = ("R_", "1HZ", "BOOM", "CRASH", "STEP", "JD")


def get_spec(symbol: str) -> InstrumentSpec:
    """
    Spec for `symbol`, falling back to a class guess for symbols outside the
    cache (e.g. BTCUSDT from an exchange export) so callers never get a KeyError.
    """
    key = (symbol or "").upper().strip()
    if key in _SPECS:
        return _SPECS[key]

    if any(key.startswith(p) for p in _SYNTHETIC_PREFIXES):
        return InstrumentSpec(
            key, "SYNTHETIC", 1.0, 4, 0.0001, 0.0001, 0.0, 0.0,
            max_leverage=1.0, min_lot=1.0, lot_step=1.0,
        )
    if "XAU" in key or "GOLD" in key:
        return _SPECS["XAUUSD"]
    if "XAG" in key or "SILVER" in key:
        return _SPECS["XAGUSD"]
    for coin in ("BTC", "ETH", "LTC", "XRP", "SOL", "DOGE", "ADA"):
        if key.startswith(coin):
            return _SPECS.get(f"{coin}USD", _SPECS["BTCUSD"])
    if len(key) == 6:
        return _fx(key)
    return InstrumentSpec(key, "INDEX", 1.0, 2, 0.01, 0.1, 2.0, 20.0, max_leverage=20.0)


def asset_class(symbol: str) -> str:
    return get_spec(symbol).asset_class


def cost_assumptions(symbol: str) -> Dict[str, Optional[float]]:
    """
    The execution assumptions for `symbol`, shaped for RunMetadata so every run
    publishes the cost model it was priced under.
    """
    spec = get_spec(symbol)
    return {
        "spread_model": "constant",
        "commission_model": "percentage",
        "commission_rate": spec.round_trip_bps / 10_000.0,
        "slippage_pips": round(spec.slippage_points * spec.point / spec.pip, 3) if spec.pip else 0.0,
    }
