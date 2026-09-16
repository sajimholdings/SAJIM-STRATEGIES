# bot_eth_momentum.py
import datetime
import logging
import config
import exchange
import indicators
import risk
import state
import notifications


def run():
    st = state.load_state("state/eth_state.json")

    # ── Pending order reconciliation ──────────────────────────────────────────
    if st.get("pending_order"):
        logging.warning(
            "pending_order flag was True on startup -- the bot may have crashed after placing "
            "an order. Check MT5 terminal for open positions and reconcile state/eth_state.json manually."
        )
        live_pos = exchange.get_open_positions(config.ETH_SYMBOL)
        if live_pos and not st.get("open_trade"):
            logging.warning(f"Found live MT5 position not in state: {live_pos}. Updating state.")
            st["open_trade"] = {
                "direction":    live_pos["direction"],
                "entry_price":  live_pos["avg_entry_price"],
                "entry_time":   datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "size_lots":    live_pos["quantity"],
                "remaining_qty": live_pos["quantity"],
                "size_usd":     0.0,
                "bars_elapsed": 0,
                "entry_atr":    0.0,
                "peak_price":   live_pos["avg_entry_price"],
                "trough_price": live_pos["avg_entry_price"],
                "partial_filled": False,
                "partial_pnl":  0.0,
                "ticket":       live_pos["ticket"],
            }
        st["pending_order"] = False
        state.save_state("state/eth_state.json", st)

    # ── Fetch candles from MT5 ────────────────────────────────────────────────
    candles = exchange.get_klines(config.ETH_SYMBOL, config.ETH_TIMEFRAME, limit=100)
    if len(candles) < 52:
        logging.warning(f"Not enough candles fetched ({len(candles)}). Skipping.")
        return

    current_forming_candle = candles[-1]

    # ── Compute indicators ────────────────────────────────────────────────────
    inds = indicators.get_latest_indicators(
        candles,
        atr_period=config.ETH_ATR_PERIOD,
        atr_ma_period=config.ETH_ATR_MA_PERIOD,
        ema_period=14,
    )
    if not inds or inds.get("atr", 0) == 0 or inds.get("atr_ma", 0) == 0:
        logging.warning("Indicators not ready. Skipping.")
        return

    current_atr = inds["atr"]  # Use closed candle ATR for trailing stop

    logging.info(
        f"Indicators -- ATR: {current_atr:.2f} | ATR_MA: {inds['atr_ma']:.2f} | "
        f"Body: {inds['candle_body']:.2f} | Close: {inds['close']:.2f}"
    )

    st["last_run"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    open_trade = st.get("open_trade")

    # ═════════════════════════════════════════════════════════════════════════
    # BRANCH A: Trade is open -- manage position
    # ═════════════════════════════════════════════════════════════════════════
    if open_trade:
        open_trade["bars_elapsed"] += 1

        direction     = open_trade["direction"]
        current_low   = current_forming_candle["low"]
        current_high  = current_forming_candle["high"]
        current_close = current_forming_candle["close"]

        # ── Update peak / trough tracking ─────────────────────────────────────
        if direction == "LONG":
            open_trade["peak_price"] = max(open_trade["peak_price"], current_high)
            trail_level = open_trade["peak_price"] - config.ETH_TRAIL_ATR_MULTIPLE * current_atr
        else:
            open_trade["trough_price"] = min(open_trade["trough_price"], current_low)
            trail_level = open_trade["trough_price"] + config.ETH_TRAIL_ATR_MULTIPLE * current_atr

        logging.info(
            f"Position update -- {direction} | Bars: {open_trade['bars_elapsed']}/{config.ETH_MAX_BARS} | "
            f"Trail: {trail_level:.2f} | "
            f"Peak/Trough: {open_trade.get('peak_price', open_trade.get('trough_price')):.2f}"
        )

        # ── Check partial TP ──────────────────────────────────────────────────
        if not open_trade["partial_filled"]:
            partial_triggered = False
            partial_price     = 0.0

            if direction == "LONG":
                target = open_trade["entry_price"] + config.ETH_PARTIAL_TP_ATR * open_trade["entry_atr"]
                if current_high >= target:
                    partial_triggered = True; partial_price = target
            else:
                target = open_trade["entry_price"] - config.ETH_PARTIAL_TP_ATR * open_trade["entry_atr"]
                if current_low <= target:
                    partial_triggered = True; partial_price = target

            if partial_triggered:
                sym_info     = exchange.get_symbol_info(config.ETH_SYMBOL)
                qty_to_close = risk.round_lot_size(
                    open_trade["remaining_qty"] * config.ETH_PARTIAL_PCT,
                    sym_info["step_size"]
                )

                if qty_to_close >= sym_info["min_qty"]:
                    st["pending_order"] = True
                    state.save_state("state/eth_state.json", st)

                    ticket       = open_trade.get("ticket", 0)
                    orig_side    = "BUY" if direction == "LONG" else "SELL"

                    if ticket:
                        fill = exchange.close_position_by_ticket(
                            ticket=ticket,
                            symbol=config.ETH_SYMBOL,
                            lots=qty_to_close,
                            original_side=orig_side,
                            magic=config.MT5_MAGIC_ETH,
                            comment="BotPartialTP",
                        )
                    else:
                        close_side = "SELL" if direction == "LONG" else "BUY"
                        fill = exchange.place_market_order(
                            config.ETH_SYMBOL, close_side, qty_to_close,
                            magic=config.MT5_MAGIC_ETH, comment="BotPartialTP"
                        )

                    entry_p   = open_trade["entry_price"]
                    exit_p    = fill["fill_price"]
                    pnl_pct   = (exit_p - entry_p) / entry_p if entry_p > 0 else 0
                    if direction == "SHORT":
                        pnl_pct = -pnl_pct
                    partial_size_usd = open_trade["size_usd"] * config.ETH_PARTIAL_PCT
                    partial_pnl  = partial_size_usd * pnl_pct
                    partial_pnl -= partial_size_usd * config.FEE_PCT * 2

                    open_trade["remaining_qty"] -= fill["executed_qty"]
                    open_trade["partial_filled"] = True
                    open_trade["partial_pnl"]    = round(partial_pnl, 4)

                    st["balance"]   += partial_pnl
                    st["total_pnl"] += partial_pnl
                    st["pending_order"] = False
                    state.save_state("state/eth_state.json", st)

                    msg = (
                        f"\U0001f4ca *ETH MOMENTUM \u2014 Partial TP Hit*\n"
                        f"Direction: {direction}\n"
                        f"Closed {config.ETH_PARTIAL_PCT * 100:.0f}% at ${exit_p:,.2f}\n"
                        f"Partial PnL: {partial_pnl:+.2f} USD\n"
                        f"Remaining: {open_trade['remaining_qty']:.4f} lots"
                    )
                    notifications.send_telegram(msg, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
                    logging.info(
                        f"Partial TP -- closed {qty_to_close:.4f} lots @ {exit_p:.2f} | "
                        f"Partial PnL: {partial_pnl:+.2f} | Remaining: {open_trade['remaining_qty']:.4f}"
                    )
                else:
                    logging.warning(f"Partial TP triggered but qty ({qty_to_close}) < min_qty. Skipping partial.")

        # ── Check trailing stop ───────────────────────────────────────────────
        exit_triggered = False
        exit_price     = 0.0
        exit_reason    = ""

        if direction == "LONG":
            if current_low <= trail_level:
                exit_triggered = True; exit_price = trail_level; exit_reason = "TRAIL"
        else:
            if current_high >= trail_level:
                exit_triggered = True; exit_price = trail_level; exit_reason = "TRAIL"

        if not exit_triggered and open_trade["bars_elapsed"] >= config.ETH_MAX_BARS:
            exit_triggered = True; exit_price = current_close; exit_reason = "TIMEOUT"

        if exit_triggered:
            qty_to_close = open_trade["remaining_qty"]

            st["pending_order"] = True
            state.save_state("state/eth_state.json", st)

            ticket    = open_trade.get("ticket", 0)
            orig_side = "BUY" if direction == "LONG" else "SELL"

            if ticket:
                fill = exchange.close_position_by_ticket(
                    ticket=ticket,
                    symbol=config.ETH_SYMBOL,
                    lots=qty_to_close,
                    original_side=orig_side,
                    magic=config.MT5_MAGIC_ETH,
                    comment=f"BotClose_{exit_reason}",
                )
            else:
                close_side = "SELL" if direction == "LONG" else "BUY"
                fill = exchange.place_market_order(
                    config.ETH_SYMBOL, close_side, qty_to_close,
                    magic=config.MT5_MAGIC_ETH, comment=f"BotClose_{exit_reason}"
                )

            actual_exit = fill["fill_price"]
            entry_p     = open_trade["entry_price"]

            # Final leg PnL
            remaining_usd = open_trade["size_usd"] * (
                open_trade["remaining_qty"] / open_trade.get("size_lots", open_trade["remaining_qty"])
            ) if open_trade.get("size_lots", 0) > 0 else open_trade["size_usd"]
            pnl_pct   = (actual_exit - entry_p) / entry_p if entry_p > 0 else 0
            if direction == "SHORT":
                pnl_pct = -pnl_pct
            final_pnl  = remaining_usd * pnl_pct
            final_pnl -= remaining_usd * config.FEE_PCT * 2

            partial_pnl = open_trade.get("partial_pnl", 0.0)
            total_pnl   = partial_pnl + final_pnl

            initial_risk_usd = config.ETH_TRAIL_ATR_MULTIPLE * open_trade["entry_atr"] * open_trade.get("size_lots", 0.01)
            pnl_r = total_pnl / initial_risk_usd if initial_risk_usd > 0 else 0.0

            st["balance"]      += final_pnl
            st["total_trades"] += 1
            st["total_pnl"]    += final_pnl

            trade_record = {
                "entry_time":    open_trade["entry_time"],
                "exit_time":     datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "direction":     direction,
                "entry_price":   entry_p,
                "exit_price":    actual_exit,
                "pnl_usd":       round(total_pnl, 4),
                "pnl_r":         round(pnl_r, 3),
                "partial_pnl":   round(partial_pnl, 4),
                "final_pnl":     round(final_pnl, 4),
                "exit_reason":   exit_reason,
                "bars_held":     open_trade["bars_elapsed"],
                "partial_filled": open_trade["partial_filled"],
                "ticket":        ticket,
            }
            st["trade_history"].append(trade_record)
            st["open_trade"]    = None
            st["pending_order"] = False
            state.save_state("state/eth_state.json", st)

            emoji        = "\u2705" if total_pnl > 0 else "\U0001f534"
            reason_label = "TRAIL STOP" if exit_reason == "TRAIL" else "TIMEOUT"
            partial_note = f"\nPartial TP: {partial_pnl:+.2f} USD" if open_trade["partial_filled"] else ""
            msg = (
                f"{emoji} *ETH MOMENTUM \u2014 {reason_label}*\n"
                f"Entry: ${entry_p:,.2f} \u2192 Exit: ${actual_exit:,.2f}\n"
                f"Final leg: {final_pnl:+.2f} USD{partial_note}\n"
                f"Total PnL: {total_pnl:+.2f} USD\n"
                f"R-Multiple: {pnl_r:+.2f}R\n"
                f"Duration: {open_trade['bars_elapsed']} bars\n"
                f"Account: ${st['balance']:,.2f}"
            )
            notifications.send_telegram(msg, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
            logging.info(
                f"Exited {direction} | {exit_reason} | "
                f"Entry: {entry_p:.2f} -> Exit: {actual_exit:.2f} | "
                f"Total PnL: {total_pnl:+.2f} USD ({pnl_r:+.2f}R)"
            )
            return

        # ── Send 6-bar position update ────────────────────────────────────────
        if open_trade["bars_elapsed"] % 6 == 0:
            entry_p   = open_trade["entry_price"]
            rem_usd   = open_trade["size_usd"] * (
                open_trade["remaining_qty"] / open_trade.get("size_lots", open_trade["remaining_qty"])
            ) if open_trade.get("size_lots", 0) > 0 else open_trade["size_usd"]
            open_pct  = (current_close - entry_p) / entry_p if entry_p > 0 else 0
            if direction == "SHORT":
                open_pct = -open_pct
            open_pnl = rem_usd * open_pct
            best     = open_trade.get("peak_price" if direction == "LONG" else "trough_price", entry_p)

            msg = (
                f"\U0001f4ca *ETH MOMENTUM \u2014 Position Update*\n"
                f"Direction: {direction}\n"
                f"Entry: ${entry_p:,.2f}\n"
                f"Current: ${current_close:,.2f}\n"
                f"Open PnL: {open_pnl:+.2f} USD ({open_pct * 100:+.1f}%)\n"
                f"Best price: ${best:,.2f}\n"
                f"Trail stop: ${trail_level:,.2f}\n"
                f"Bars held: {open_trade['bars_elapsed']}/{config.ETH_MAX_BARS}"
            )
            notifications.send_telegram(msg, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)

        st["open_trade"] = open_trade
        state.save_state("state/eth_state.json", st)
        return

    # ═════════════════════════════════════════════════════════════════════════
    # BRANCH B: No open trade -- check for entry signal
    # ═════════════════════════════════════════════════════════════════════════
    atr    = inds["atr"]
    atr_ma = inds["atr_ma"]
    body   = inds["candle_body"]
    close  = inds["close"]

    # ── 1. Volatility regime filter ───────────────────────────────────────────
    if atr <= config.ETH_VOL_THRESHOLD * atr_ma:
        logging.info(f"Low vol regime -- ATR ({atr:.2f}) <= {config.ETH_VOL_THRESHOLD}x ATR_MA ({atr_ma:.2f}). No trade.")
        state.save_state("state/eth_state.json", st)
        return

    # ── 2. Candle body trigger ────────────────────────────────────────────────
    if abs(body) <= config.ETH_BODY_ATR_ENTRY * atr:
        logging.info(
            f"No signal -- body ({abs(body):.2f}) <= {config.ETH_BODY_ATR_ENTRY}x ATR ({atr:.2f}). "
            f"Need body > {config.ETH_BODY_ATR_ENTRY * atr:.2f}"
        )
        state.save_state("state/eth_state.json", st)
        return

    # ── 3. Direction: FOLLOW the candle (momentum) ────────────────────────────
    direction = "LONG" if body > 0 else "SHORT"
    logging.info(f"Signal triggered! Body={body:.2f}, ATR={atr:.2f} -> {direction}")

    # ── 4. Initial SL for sizing ──────────────────────────────────────────────
    if direction == "LONG":
        sl_for_sizing = close - config.ETH_TRAIL_ATR_MULTIPLE * atr
    else:
        sl_for_sizing = close + config.ETH_TRAIL_ATR_MULTIPLE * atr

    # ── 5. MT5 lot sizing ────────────────────────────────────────────────────
    sym_info = exchange.get_symbol_info(config.ETH_SYMBOL)
    equity   = exchange.get_account_balance()

    if equity <= 0:
        logging.warning("Account equity is zero. Cannot size position.")
        state.save_state("state/eth_state.json", st)
        return

    size_lots, size_usd = risk.calculate_lots_mt5(
        equity, close, sl_for_sizing,
        config.RISK_PER_TRADE, config.MAX_POSITION_PCT, sym_info
    )

    if size_lots < sym_info["min_qty"]:
        logging.warning(
            f"Calculated lots ({size_lots:.4f}) < MT5 min_qty ({sym_info['min_qty']}). Skipping."
        )
        state.save_state("state/eth_state.json", st)
        return

    # ── 6. Place entry order ──────────────────────────────────────────────────
    st["pending_order"] = True
    state.save_state("state/eth_state.json", st)

    mt5_side = "BUY" if direction == "LONG" else "SELL"
    fill = exchange.place_market_order(
        config.ETH_SYMBOL, mt5_side, size_lots,
        magic=config.MT5_MAGIC_ETH, comment="BotEntry"
    )

    # ── 7. Record open trade ──────────────────────────────────────────────────
    partial_target = (
        fill["fill_price"] + config.ETH_PARTIAL_TP_ATR * atr
        if direction == "LONG"
        else fill["fill_price"] - config.ETH_PARTIAL_TP_ATR * atr
    )

    st["open_trade"] = {
        "direction":      direction,
        "entry_price":    fill["fill_price"],
        "entry_time":     datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "size_lots":      fill["executed_qty"],
        "remaining_qty":  fill["executed_qty"],
        "size_usd":       size_usd,
        "bars_elapsed":   0,
        "entry_atr":      atr,
        "peak_price":     fill["fill_price"],
        "trough_price":   fill["fill_price"],
        "partial_filled": False,
        "partial_pnl":    0.0,
        "ticket":         fill.get("ticket", 0),
    }
    st["pending_order"] = False
    state.save_state("state/eth_state.json", st)

    risk_pct = config.RISK_PER_TRADE * 100
    msg = (
        f"\U0001f7e2 *ETH MOMENTUM \u2014 {direction}*\n"
        f"Entry: ${fill['fill_price']:,.2f}\n"
        f"Trail: {config.ETH_TRAIL_ATR_MULTIPLE}x ATR (${config.ETH_TRAIL_ATR_MULTIPLE * atr:.2f})\n"
        f"Partial TP: ${partial_target:,.2f} ({config.ETH_PARTIAL_TP_ATR}x ATR)\n"
        f"Size: {size_lots:.4f} lots (${size_usd:.2f})\n"
        f"Risk: {risk_pct:.1f}% of equity\n"
        f"Ticket: {fill.get('ticket', 'N/A')}"
    )
    notifications.send_telegram(msg, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    logging.info(
        f"Entered {direction} @ {fill['fill_price']:.2f} | Trail: {config.ETH_TRAIL_ATR_MULTIPLE}x ATR | "
        f"Lots: {size_lots:.4f} (${size_usd:.2f}) | Ticket: {fill.get('ticket', 'N/A')}"
    )
