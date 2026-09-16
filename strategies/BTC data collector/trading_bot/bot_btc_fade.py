# bot_btc_fade.py
import datetime
import logging
import config
import exchange
import indicators
import risk
import state
import notifications


def run():
    st = state.load_state("state/btc_state.json")

    # ── Pending order reconciliation ──────────────────────────────────────────
    if st.get("pending_order"):
        logging.warning(
            "pending_order flag was True on startup -- the bot may have crashed after placing "
            "an order. Check MT5 terminal for open positions and reconcile state/btc_state.json manually."
        )
        # Check MT5 directly for an open position we might have missed
        live_pos = exchange.get_open_positions(config.BTC_SYMBOL)
        if live_pos and not st.get("open_trade"):
            logging.warning(
                f"Found live MT5 position not in state: {live_pos}. "
                f"Updating state to reflect it."
            )
            st["open_trade"] = {
                "direction":   live_pos["direction"],
                "entry_price": live_pos["avg_entry_price"],
                "entry_time":  datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "sl_price":    0.0,  # Unknown -- manual intervention required
                "tp_price":    0.0,
                "size_lots":   live_pos["quantity"],
                "size_usd":    0.0,
                "bars_elapsed": 0,
                "entry_atr":   0.0,
                "ticket":      live_pos["ticket"],
            }
        st["pending_order"] = False
        state.save_state("state/btc_state.json", st)

    # ── Fetch candles from MT5 ─────────────────────────────────────────────────
    candles = exchange.get_klines(config.BTC_SYMBOL, config.BTC_TIMEFRAME, limit=100)
    if len(candles) < 52:
        logging.warning(f"Not enough candles fetched ({len(candles)}). Skipping.")
        return

    # candles[-1] = currently forming (incomplete) -- never use for entry decisions
    # candles[-2] = last fully closed candle -- all decisions based on this
    current_forming_candle = candles[-1]

    # ── Compute indicators ────────────────────────────────────────────────────
    inds = indicators.get_latest_indicators(
        candles,
        atr_period=config.BTC_ATR_PERIOD,
        atr_ma_period=config.BTC_ATR_MA_PERIOD,
        ema_period=config.BTC_EMA_PERIOD,
    )
    if not inds or inds.get("atr", 0) == 0 or inds.get("atr_ma", 0) == 0:
        logging.warning("Indicators not ready. Skipping.")
        return

    logging.info(
        f"Indicators -- ATR: {inds['atr']:.2f} | ATR_MA: {inds['atr_ma']:.2f} | "
        f"EMA: {inds['ema']:.2f} | Body: {inds['candle_body']:.2f} | Close: {inds['close']:.2f}"
    )

    st["last_run"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    open_trade = st.get("open_trade")

    # ═════════════════════════════════════════════════════════════════════════
    # BRANCH A: Trade is open -- check for exit
    # ═════════════════════════════════════════════════════════════════════════
    if open_trade:
        open_trade["bars_elapsed"] += 1

        direction = open_trade["direction"]
        sl_price  = open_trade["sl_price"]
        tp_price  = open_trade["tp_price"]

        # Use the forming candle's range to check if SL/TP was touched this bar
        current_low   = current_forming_candle["low"]
        current_high  = current_forming_candle["high"]
        current_close = current_forming_candle["close"]

        exit_triggered = False
        exit_price     = 0.0
        exit_reason    = ""

        if direction == "LONG":
            if sl_price > 0 and current_low <= sl_price:
                exit_triggered = True; exit_price = sl_price; exit_reason = "SL"
            elif tp_price > 0 and current_high >= tp_price:
                exit_triggered = True; exit_price = tp_price; exit_reason = "TP"
        else:  # SHORT
            if sl_price > 0 and current_high >= sl_price:
                exit_triggered = True; exit_price = sl_price; exit_reason = "SL"
            elif tp_price > 0 and current_low <= tp_price:
                exit_triggered = True; exit_price = tp_price; exit_reason = "TP"

        if not exit_triggered and open_trade["bars_elapsed"] >= config.BTC_MAX_BARS:
            exit_triggered = True; exit_price = current_close; exit_reason = "TIMEOUT"

        if exit_triggered:
            # ── Save pending flag BEFORE placing close order ──────────────────
            st["pending_order"] = True
            state.save_state("state/btc_state.json", st)

            # ── Close position via MT5 by ticket ─────────────────────────────
            original_mt5_side = "BUY" if direction == "LONG" else "SELL"
            ticket = open_trade.get("ticket", 0)

            if ticket:
                fill = exchange.close_position_by_ticket(
                    ticket=ticket,
                    symbol=config.BTC_SYMBOL,
                    lots=open_trade["size_lots"],
                    original_side=original_mt5_side,
                    magic=config.MT5_MAGIC_BTC,
                    comment=f"BotClose_{exit_reason}",
                )
            else:
                # Fallback: open a countertrade (no ticket stored)
                close_side = "SELL" if direction == "LONG" else "BUY"
                fill = exchange.place_market_order(
                    config.BTC_SYMBOL, close_side, open_trade["size_lots"],
                    magic=config.MT5_MAGIC_BTC, comment=f"BotClose_{exit_reason}"
                )

            actual_exit = fill["fill_price"]

            # ── PnL calculation ───────────────────────────────────────────────
            # For CFDs: PnL = lots * contract_size * (exit - entry) * direction_sign
            # Simplified: use size_usd as the notional
            entry_p  = open_trade["entry_price"]
            pnl_pct  = (actual_exit - entry_p) / entry_p if entry_p > 0 else 0
            if direction == "SHORT":
                pnl_pct = -pnl_pct
            pnl_usd  = open_trade["size_usd"] * pnl_pct
            pnl_usd -= open_trade["size_usd"] * config.FEE_PCT * 2  # entry + exit fees

            risk_usd = abs(open_trade["entry_price"] - sl_price) * open_trade.get("size_lots", 0.01)
            pnl_r    = pnl_usd / risk_usd if risk_usd > 0 else 0.0

            # ── Update state ──────────────────────────────────────────────────
            st["balance"]      += pnl_usd
            st["total_trades"] += 1
            st["total_pnl"]    += pnl_usd

            trade_record = {
                "entry_time":  open_trade["entry_time"],
                "exit_time":   datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "direction":   direction,
                "entry_price": entry_p,
                "exit_price":  actual_exit,
                "pnl_usd":     round(pnl_usd, 4),
                "pnl_r":       round(pnl_r, 3),
                "exit_reason": exit_reason,
                "bars_held":   open_trade["bars_elapsed"],
                "ticket":      ticket,
            }
            st["trade_history"].append(trade_record)
            st["open_trade"]    = None
            st["pending_order"] = False
            state.save_state("state/btc_state.json", st)

            emoji = "\u2705" if pnl_usd > 0 else "\U0001f534"
            reason_label = {"TP": "TP HIT", "SL": "SL HIT", "TIMEOUT": "TIMEOUT"}.get(exit_reason, exit_reason)
            msg = (
                f"{emoji} *BTC FADE \u2014 {reason_label}*\n"
                f"Entry: ${entry_p:,.2f} \u2192 Exit: ${actual_exit:,.2f}\n"
                f"PnL: {pnl_usd:+.2f} USD ({pnl_pct * 100:+.2f}%)\n"
                f"R-Multiple: {pnl_r:+.2f}R\n"
                f"Duration: {open_trade['bars_elapsed']} bars\n"
                f"Account: ${st['balance']:,.2f}"
            )
            notifications.send_telegram(msg, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
            logging.info(
                f"Exited {direction} | Reason: {exit_reason} | "
                f"Entry: {entry_p:.2f} -> Exit: {actual_exit:.2f} | "
                f"PnL: {pnl_usd:+.2f} USD ({pnl_r:+.2f}R)"
            )
            return

        # No exit -- save updated state
        st["open_trade"] = open_trade
        state.save_state("state/btc_state.json", st)
        logging.info(
            f"Trade open -- {direction} | SL: {sl_price:.2f} | TP: {tp_price:.2f} | "
            f"Bars: {open_trade['bars_elapsed']}/{config.BTC_MAX_BARS} | "
            f"Ticket: {open_trade.get('ticket', 'N/A')}"
        )
        return

    # ═════════════════════════════════════════════════════════════════════════
    # BRANCH B: No open trade -- check for entry signal
    # ═════════════════════════════════════════════════════════════════════════
    atr    = inds["atr"]
    atr_ma = inds["atr_ma"]
    ema    = inds["ema"]
    body   = inds["candle_body"]
    close  = inds["close"]
    high   = inds["high"]
    low    = inds["low"]

    # ── 1. Volatility regime filter ───────────────────────────────────────────
    if atr <= config.BTC_VOL_THRESHOLD * atr_ma:
        logging.info(f"Low vol regime -- ATR ({atr:.2f}) <= {config.BTC_VOL_THRESHOLD}x ATR_MA ({atr_ma:.2f}). No trade.")
        state.save_state("state/btc_state.json", st)
        return

    # ── 2. Candle body trigger ────────────────────────────────────────────────
    if abs(body) <= config.BTC_BODY_ATR_ENTRY * atr:
        logging.info(
            f"No signal -- body ({abs(body):.2f}) <= {config.BTC_BODY_ATR_ENTRY}x ATR ({atr:.2f}). "
            f"Need body > {config.BTC_BODY_ATR_ENTRY * atr:.2f}"
        )
        state.save_state("state/btc_state.json", st)
        return

    # ── 3. Direction: FADE the candle ─────────────────────────────────────────
    direction = "SHORT" if body > 0 else "LONG"
    logging.info(f"Signal triggered! Body={body:.2f}, ATR={atr:.2f} -> {direction}")

    # ── 4. Calculate SL and TP levels ─────────────────────────────────────────
    if direction == "LONG":
        sl   = low - config.BTC_SL_ATR_MULTIPLE * atr
        dist = ema - close
        if dist <= 0:
            logging.info(f"LONG signal skipped -- EMA ({ema:.2f}) is below close ({close:.2f}).")
            state.save_state("state/btc_state.json", st)
            return
        tp = close + dist * config.BTC_TP_MEAN_FRACTION
    else:  # SHORT
        sl   = high + config.BTC_SL_ATR_MULTIPLE * atr
        dist = close - ema
        if dist <= 0:
            logging.info(f"SHORT signal skipped -- EMA ({ema:.2f}) is above close ({close:.2f}).")
            state.save_state("state/btc_state.json", st)
            return
        tp = close - dist * config.BTC_TP_MEAN_FRACTION

    # ── 5. Risk/reward filter ─────────────────────────────────────────────────
    reward    = abs(tp - close)
    risk_dist = abs(close - sl)
    rr        = reward / risk_dist if risk_dist > 0 else 0.0
    if rr < 1.0:
        logging.info(f"RR ({rr:.2f}) < 1.0 -- skipping.")
        state.save_state("state/btc_state.json", st)
        return

    # ── 6. MT5 lot sizing using tick value ────────────────────────────────────
    sym_info   = exchange.get_symbol_info(config.BTC_SYMBOL)
    equity     = exchange.get_account_balance()

    if equity <= 0:
        logging.warning("Account equity is zero. Cannot size position.")
        state.save_state("state/btc_state.json", st)
        return

    size_lots, size_usd = risk.calculate_lots_mt5(
        equity, close, sl,
        config.RISK_PER_TRADE, config.MAX_POSITION_PCT, sym_info
    )

    if size_lots < sym_info["min_qty"]:
        logging.warning(
            f"Calculated lots ({size_lots:.4f}) < MT5 min_qty ({sym_info['min_qty']}). "
            f"Account equity may be too small. Skipping."
        )
        state.save_state("state/btc_state.json", st)
        return

    # ── 7. Place entry order via MT5 ─────────────────────────────────────────
    st["pending_order"] = True
    state.save_state("state/btc_state.json", st)

    mt5_side = "BUY" if direction == "LONG" else "SELL"
    fill = exchange.place_market_order(
        config.BTC_SYMBOL, mt5_side, size_lots,
        magic=config.MT5_MAGIC_BTC, comment="BotEntry"
    )

    # ── 8. Record open trade in state ────────────────────────────────────────
    st["open_trade"] = {
        "direction":   direction,
        "entry_price": fill["fill_price"],
        "entry_time":  datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "sl_price":    sl,
        "tp_price":    tp,
        "size_lots":   fill["executed_qty"],
        "size_usd":    size_usd,
        "bars_elapsed": 0,
        "entry_atr":   atr,
        "ticket":      fill.get("ticket", 0),
    }
    st["pending_order"] = False
    state.save_state("state/btc_state.json", st)

    # ── 9. Telegram entry alert ───────────────────────────────────────────────
    risk_pct = config.RISK_PER_TRADE * 100
    msg = (
        f"\U0001f535 *BTC FADE \u2014 {direction}*\n"
        f"Entry: ${fill['fill_price']:,.2f}\n"
        f"SL: ${sl:,.2f} ({config.BTC_SL_ATR_MULTIPLE}x ATR)\n"
        f"TP: ${tp:,.2f} ({config.BTC_TP_MEAN_FRACTION}x to EMA)\n"
        f"Size: {size_lots:.4f} lots (${size_usd:.2f})\n"
        f"Risk: {risk_pct:.1f}% of equity\n"
        f"RR: 1:{rr:.1f}\n"
        f"Ticket: {fill.get('ticket', 'N/A')}"
    )
    notifications.send_telegram(msg, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    logging.info(
        f"Entered {direction} @ {fill['fill_price']:.2f} | SL: {sl:.2f} | TP: {tp:.2f} | "
        f"Lots: {size_lots:.4f} (${size_usd:.2f}) | RR: 1:{rr:.2f} | Ticket: {fill.get('ticket', 'N/A')}"
    )
