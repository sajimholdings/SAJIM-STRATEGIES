import MetaTrader5 as mt5

class MT5Connector:
    def __init__(self):
        pass
        
    def initialize(self) -> bool:
        return mt5.initialize()
        
    def shutdown(self):
        mt5.shutdown()
        
    def get_positions(self, symbol: str):
        return mt5.positions_get(symbol=symbol)
        
    def submit_order(self, symbol: str, order_type: str, lots: float, sl: float, tp: float):
        otype = mt5.ORDER_TYPE_BUY if order_type == "BUY" else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            print(f"Failed to get tick for {symbol}")
            return None
            
        price = tick.ask if order_type == "BUY" else tick.bid
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lots,
            "type": otype,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": 200000,
            "comment": "Sovereign v2",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"Order executed: {result.order}")
        else:
            print(f"Order failed: {result}")
        return result
