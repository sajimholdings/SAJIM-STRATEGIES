# risk.py
import math
import logging


def round_lot_size(lots: float, volume_step: float) -> float:
    """
    Round lot size DOWN to the nearest valid MT5 volume step.
    e.g. lots=0.037, volume_step=0.01 -> 0.03
    Uses log10 to safely handle steps like 0.001 that Python formats as '1e-03'.
    """
    if volume_step <= 0:
        return lots
    precision = max(0, -int(math.floor(math.log10(volume_step))))
    factor = 10 ** precision
    return math.floor(lots * factor) / factor


def round_step_size(quantity: float, step_size: float) -> float:
    """Alias for round_lot_size — kept for backward compatibility."""
    return round_lot_size(quantity, step_size)


def calculate_lots_mt5(
    equity: float,
    entry_price: float,
    sl_price: float,
    risk_per_trade: float,
    max_position_pct: float,
    sym_info: dict,
) -> tuple[float, float]:
    """
    Calculate MT5 lot size from USD risk and tick value.

    MT5 CFD lot sizing formula:
        risk_usd       = equity * risk_per_trade
        sl_distance    = abs(entry - sl)
        risk_per_lot   = (sl_distance / tick_size) * tick_value
        lots           = risk_usd / risk_per_lot

    Then cap by:
        max_position_usd = equity * max_position_pct
        lots             = min(lots, max_position_usd / (contract_size * entry_price))

    Returns (lots, position_value_usd).
    """
    tick_size     = sym_info.get("tick_size", 0.01)
    tick_value    = sym_info.get("tick_value", 1.0)
    contract_size = sym_info.get("contract_size", 1.0)
    volume_step   = sym_info.get("step_size", 0.01)
    min_lots      = sym_info.get("min_qty", 0.01)
    max_lots      = sym_info.get("max_qty", 100.0)

    sl_distance = abs(entry_price - sl_price)
    if sl_distance == 0 or tick_size == 0 or tick_value == 0:
        logging.warning("calculate_lots_mt5: zero SL distance or tick info. Cannot size position.")
        return 0.0, 0.0

    risk_usd     = equity * risk_per_trade
    risk_per_lot = (sl_distance / tick_size) * tick_value

    lots_by_risk = risk_usd / risk_per_lot

    # Cap by max position percentage
    max_usd  = equity * max_position_pct
    # Position value = lots * contract_size * entry_price
    lots_by_cap = max_usd / (contract_size * entry_price) if contract_size * entry_price > 0 else lots_by_risk

    lots = min(lots_by_risk, lots_by_cap)
    lots = max(min_lots, min(lots, max_lots))
    lots = round_lot_size(lots, volume_step)

    position_value_usd = lots * contract_size * entry_price
    return lots, position_value_usd


def calculate_position_size(
    balance: float,
    entry_price: float,
    sl_price: float,
    risk_per_trade: float,
    max_position_pct: float,
    step_size: float,
) -> tuple[float, float]:
    """
    Legacy interface kept for backward compatibility with bot files.
    Works as a simple quantity-based sizer — used internally when sym_info
    is not fully available (e.g. in validate.py tests).
    Bot files use calculate_lots_mt5() via the sym_info path.
    """
    risk_usd      = balance * risk_per_trade
    risk_per_unit = abs(entry_price - sl_price)

    if risk_per_unit == 0:
        return 0.0, 0.0

    size_qty_theoretical = risk_usd / risk_per_unit
    size_usd_theoretical = size_qty_theoretical * entry_price

    max_usd  = balance * max_position_pct
    size_usd = min(size_usd_theoretical, max_usd)

    size_qty = size_usd / entry_price
    size_qty = round_lot_size(size_qty, step_size)
    size_usd_actual = size_qty * entry_price

    return size_qty, size_usd_actual
