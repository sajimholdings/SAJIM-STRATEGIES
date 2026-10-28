//+------------------------------------------------------------------+
//|                                          MCP_Scalper_Sajim.mq5   |
//|                                    SAJIM Quant - MCP Pipeline Test|
//|                                        https://sajimholdings.com |
//+------------------------------------------------------------------+
#property copyright "SAJIM Quant Lab"
#property link      "https://sajimholdings.com"
#property version   "1.30"
#property description "EMA Pullback Micro-Scalper v1.30"
#property description "Features: Dynamic % Compounding, Auto-Session Timing, Trailing Runner, US30 Spread Fix"
#property strict

#include <Trade\Trade.mqh>

//+------------------------------------------------------------------+
//| Input Parameters                                                  |
//+------------------------------------------------------------------+
input group "=== Entry Logic ==="
input int    InpEMA_Trend       = 50;          // Trend EMA Period
input int    InpEMA_Signal      = 21;          // Signal EMA Period (pullback target)
input int    InpRSI_Period      = 14;          // RSI Period
input double InpRSI_Lower       = 40.0;        // RSI Lower Band (pullback zone)
input double InpRSI_Upper       = 60.0;        // RSI Upper Band (pullback zone)

input group "=== Risk Management & Position Sizing ==="
input bool   InpUseDynamicRisk     = true;     // Use Dynamic % Risk Sizing (true) vs Fixed Lot (false)
input double InpRiskPercent        = 1.5;      // Risk % per Trade (e.g. 1.5% - 2.0%)
input double InpLotSize            = 0.10;     // Fallback Fixed Lot Size (if dynamic risk disabled)
input double InpSL_ATR_Mult        = 1.0;      // Stop Loss (ATR multiplier = 1R)
input double InpTP_ATR_Mult        = 3.0;      // Initial Take Profit (ATR multiplier = 3R)
input int    InpATR_Period         = 14;       // ATR Period
input double InpBreakeven_R        = 1.5;      // Breakeven Trigger (in R multiples, 1.5 = +1.5R)
input double InpBE_Buffer_Points   = 10;       // Breakeven Buffer (points above/below open price)

input group "=== Partial Close (Two-Target Exit) ==="
input bool   InpPartialClose_Enable = true;    // Enable partial close at first target
input double InpPartialClose_R      = 1.5;     // Partial Close Trigger (in R multiples, e.g. 1.5)
input double InpPartialClose_Pct    = 50.0;    // Partial Close Size (% of position, e.g. 50)

input group "=== Dynamic Trailing Runner (Home Run Expander) ==="
input bool   InpTrailRunner_Enable = true;     // Trail Remaining 50% Runner (Unlocks +5R to +8R Home Runs)
input double InpTrailRunner_StartR = 2.0;      // Runner Trail Start (in R multiples, e.g. 2.0R)
input double InpTrailRunner_ATR    = 1.0;      // Runner Trailing Distance (ATR behind price)

input group "=== Session Timing Filters ==="
input bool   InpUseSessionFilter    = true;    // Enable Session Time Filter
input bool   InpAutoSessionBySymbol = true;    // Auto-Apply Empirically Proven Windows per Symbol
input int    InpSessionStartHour    = 7;       // Manual Session Start Hour (UTC)
input int    InpSessionStartMin     = 0;       // Manual Session Start Minute
input int    InpSessionEndHour      = 16;      // Manual Session End Hour (UTC)
input int    InpSessionEndMin       = 30;      // Manual Session End Minute

input group "=== Filters ==="
input bool           InpUseHTF_Filter    = true;        // Enable HTF Trend Filter
input ENUM_TIMEFRAMES InpHTF_Timeframe   = PERIOD_H1;   // HTF Timeframe (e.g. H1)
input int            InpHTF_EMA_Period   = 100;         // HTF EMA Period (e.g. 100)
input int            InpMaxTradesPerDay  = 0;           // Max Trades Per Day (0 = Unlimited)
input int            InpMaxSpreadPoints  = 50;          // Max Spread (points, auto-expands for indices)

input group "=== Decision Journal & Forensics ==="
input bool   InpLogBarDiagnostics = true;                     // Print bar filter diagnostics on each M5 close
input bool   InpLogToCommonCsv    = true;                     // Log trade events to Common/Files CSV
input string InpLogFileName       = "MCP_Scalper_Journal.csv"; // CSV Journal Filename

input group "=== System ==="
input ulong  InpMagicNumber       = 20261006;   // Magic Number

//+------------------------------------------------------------------+
//| Global Variables                                                  |
//+------------------------------------------------------------------+
int g_ema_trend_handle;
int g_ema_signal_handle;
int g_rsi_handle;
int g_atr_handle;
int g_htf_ema_handle = INVALID_HANDLE;

CTrade g_trade;
datetime g_last_bar_time;
int g_trades_today;
datetime g_last_trade_date;

// Effective spread limit
int g_effective_max_spread = 50;

// Partial close & runner tracking
ulong g_partial_done_ticket = 0;
bool  g_runner_unlocked_tp  = false;

// Trade Forensics tracking for active trade
ulong    g_active_ticket       = 0;
datetime g_active_entry_time   = 0;
double   g_active_entry_price  = 0.0;
double   g_active_entry_sl     = 0.0;
double   g_active_entry_tp     = 0.0;
double   g_active_entry_rsi    = 0.0;
double   g_active_entry_atr    = 0.0;
double   g_active_initial_risk = 0.0;
double   g_active_initial_lots = 0.0;
string   g_active_direction    = "";

//+------------------------------------------------------------------+
//| Helper: Append Event to Common CSV File                           |
//+------------------------------------------------------------------+
void AppendJournalCsv(string event_type, ulong ticket, string dir, double lots,
                      double price, double sl, double tp, double rsi_val,
                      double atr_val, double spread_pts, double pnl,
                      double r_mult, string notes)
{
   if(!InpLogToCommonCsv) return;

   int handle = FileOpen(InpLogFileName, FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("WARNING: Unable to open CSV journal file: ", InpLogFileName, " Error=", GetLastError());
      return;
   }

   // If new or empty file, write CSV header
   if(FileSize(handle) == 0)
   {
      FileWrite(handle,
                "Timestamp", "Ticket", "Event", "Direction", "Volume",
                "Price", "SL", "TP", "RSI", "ATR", "Spread_Pts",
                "PnL_USD", "R_Return", "Notes");
   }

   FileSeek(handle, 0, SEEK_END);
   FileWrite(handle,
             TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS),
             IntegerToString(ticket),
             event_type,
             dir,
             DoubleToString(lots, 2),
             DoubleToString(price, _Digits),
             DoubleToString(sl, _Digits),
             DoubleToString(tp, _Digits),
             DoubleToString(rsi_val, 1),
             DoubleToString(atr_val, 2),
             DoubleToString(spread_pts, 0),
             DoubleToString(pnl, 2),
             DoubleToString(r_mult, 2),
             notes);

   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Session Timing Windows Configuration                              |
//+------------------------------------------------------------------+
void GetSessionWindow(int &start_h, int &start_m, int &end_h, int &end_m, string &desc)
{
   if(!InpUseSessionFilter)
   {
      start_h = 0; start_m = 0; end_h = 24; end_m = 0;
      desc = "24h Unrestricted (Session filter disabled)";
      return;
   }

   if(InpAutoSessionBySymbol)
   {
      string sym = _Symbol;
      StringToUpper(sym);

      // EURUSD: Baseline data collection mode (runs 24h unrestricted per user request)
      if(StringFind(sym, "EUR") >= 0)
      {
         start_h = 0; start_m = 0; end_h = 24; end_m = 0;
         desc = "24h Baseline Data Collection Mode (EURUSD special rule)";
      }
      // XAUUSD: London Morning + US Morning prime (stops at 16:00 to prevent 17:00+ US afternoon -$106 crash)
      else if(StringFind(sym, "XAU") >= 0 || StringFind(sym, "GOLD") >= 0)
      {
         start_h = 6; start_m = 30; end_h = 16; end_m = 0;
         desc = "06:30 - 16:00 UTC (Gold London/NY Prime; cuts 17:00+ afternoon chop)";
      }
      // GBPUSD: London Morning + Overlap (75% win rate window)
      else if(StringFind(sym, "GBP") >= 0)
      {
         start_h = 6; start_m = 30; end_h = 16; end_m = 30;
         desc = "06:30 - 16:30 UTC (GBP London/NY Prime; 75% win rate window)";
      }
      // USDJPY: London + US Overlap (completely cuts 0% win rate Asian session chop)
      else if(StringFind(sym, "JPY") >= 0)
      {
         start_h = 7; start_m = 0; end_h = 17; end_m = 0;
         desc = "07:00 - 17:00 UTC (JPY London/NY Prime; blocks 0% Asian session chop)";
      }
      // US30: US Cash Market Open (13:30 - 18:00 UTC)
      else if(StringFind(sym, "30") >= 0 || StringFind(sym, "DOW") >= 0 || StringFind(sym, "DJ") >= 0)
      {
         start_h = 13; start_m = 30; end_h = 18; end_m = 0;
         desc = "13:30 - 18:00 UTC (US30 Cash Market Open)";
      }
      else
      {
         start_h = InpSessionStartHour; start_m = InpSessionStartMin;
         end_h   = InpSessionEndHour;   end_m   = InpSessionEndMin;
         desc = StringFormat("%02d:%02d - %02d:%02d UTC (Manual Input)", start_h, start_m, end_h, end_m);
      }
   }
   else
   {
      start_h = InpSessionStartHour; start_m = InpSessionStartMin;
      end_h   = InpSessionEndHour;   end_m   = InpSessionEndMin;
      desc = StringFormat("%02d:%02d - %02d:%02d UTC (Manual Input)", start_h, start_m, end_h, end_m);
   }
}

bool IsWithinTradingSession()
{
   int sh, sm, eh, em;
   string desc;
   GetSessionWindow(sh, sm, eh, em, desc);

   if(sh == 0 && sm == 0 && eh == 24 && em == 0)
      return true;

   MqlDateTime dt;
   TimeGMT(dt);
   int cur = dt.hour * 60 + dt.min;
   int st  = sh * 60 + sm;
   int en  = eh * 60 + em;

   if(st <= en)
      return (cur >= st && cur < en);
   else
      return (cur >= st || cur < en);
}

//+------------------------------------------------------------------+
//| Dynamic Compound Position Sizing Calculation                     |
//+------------------------------------------------------------------+
double CalculatePositionSize(double sl_distance_price)
{
   if(!InpUseDynamicRisk || InpRiskPercent <= 0)
      return InpLotSize;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity <= 0) equity = AccountInfoDouble(ACCOUNT_BALANCE);

   double risk_amount = equity * (InpRiskPercent / 100.0);
   if(risk_amount <= 0 || sl_distance_price <= 0) return InpLotSize;

   double current_price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double risk_per_lot = 0.0;
   
   if(!OrderCalcProfit(ORDER_TYPE_BUY, _Symbol, 1.0, current_price, current_price - sl_distance_price, risk_per_lot))
   {
      double tick_size  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      if(tick_size > 0 && tick_value > 0)
         risk_per_lot = (sl_distance_price / tick_size) * tick_value;
   }

   risk_per_lot = MathAbs(risk_per_lot);
   if(risk_per_lot <= 0) return InpLotSize;

   double calc_lots = risk_amount / risk_per_lot;

   double min_lot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(lot_step <= 0) lot_step = 0.01;

   calc_lots = MathFloor(calc_lots / lot_step) * lot_step;
   calc_lots = MathMax(min_lot * 2.0, MathMin(max_lot, calc_lots)); // Ensure splittable for Option A 50/50

   return NormalizeDouble(calc_lots, 2);
}

//+------------------------------------------------------------------+
//| Expert initialization function                                    |
//+------------------------------------------------------------------+
int OnInit()
{
   //--- Create indicator handles
   g_ema_trend_handle  = iMA(_Symbol, PERIOD_CURRENT, InpEMA_Trend, 0, MODE_EMA, PRICE_CLOSE);
   g_ema_signal_handle = iMA(_Symbol, PERIOD_CURRENT, InpEMA_Signal, 0, MODE_EMA, PRICE_CLOSE);
   g_rsi_handle        = iRSI(_Symbol, PERIOD_CURRENT, InpRSI_Period, PRICE_CLOSE);
   g_atr_handle        = iATR(_Symbol, PERIOD_CURRENT, InpATR_Period);

   if(g_ema_trend_handle == INVALID_HANDLE || g_ema_signal_handle == INVALID_HANDLE ||
      g_rsi_handle == INVALID_HANDLE || g_atr_handle == INVALID_HANDLE)
   {
      Print("ERROR: Failed to create indicator handles");
      return(INIT_FAILED);
   }

   //--- Create HTF indicator handle if enabled
   if(InpUseHTF_Filter)
   {
      g_htf_ema_handle = iMA(_Symbol, InpHTF_Timeframe, InpHTF_EMA_Period, 0, MODE_EMA, PRICE_CLOSE);
      if(g_htf_ema_handle == INVALID_HANDLE)
      {
         Print("ERROR: Failed to create HTF EMA handle");
         return(INIT_FAILED);
      }
   }

   //--- Configure trade object
   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetDeviationInPoints(10);
   uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)
      g_trade.SetTypeFilling(ORDER_FILLING_FOK);
   else if((filling & SYMBOL_FILLING_IOC) != 0)
      g_trade.SetTypeFilling(ORDER_FILLING_IOC);
   else
      g_trade.SetTypeFilling(ORDER_FILLING_RETURN);

   //--- Auto-adjust spread for Index contracts (US30 / DOW)
   string sym_upper = _Symbol;
   StringToUpper(sym_upper);
   if((StringFind(sym_upper, "30") >= 0 || StringFind(sym_upper, "DOW") >= 0 || StringFind(sym_upper, "DJ") >= 0) && InpMaxSpreadPoints <= 50)
   {
      g_effective_max_spread = 250;
      PrintFormat("[INIT CONFIG] %s is an Equity Index: Spread limit expanded from %d to 250 points", _Symbol, InpMaxSpreadPoints);
   }
   else
   {
      g_effective_max_spread = InpMaxSpreadPoints;
   }

   //--- Initialize state
   g_last_bar_time       = 0;
   g_trades_today        = 0;
   g_last_trade_date     = 0;
   g_partial_done_ticket = 0;
   g_runner_unlocked_tp  = false;

   // Check if existing position is active to restore forensic tracker
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t > 0 && PositionGetInteger(POSITION_MAGIC) == (long)InpMagicNumber && PositionGetString(POSITION_SYMBOL) == _Symbol)
      {
         g_active_ticket       = t;
         g_active_entry_time   = (datetime)PositionGetInteger(POSITION_TIME);
         g_active_entry_price  = PositionGetDouble(POSITION_PRICE_OPEN);
         g_active_entry_sl     = PositionGetDouble(POSITION_SL);
         g_active_entry_tp     = PositionGetDouble(POSITION_TP);
         g_active_initial_lots = PositionGetDouble(POSITION_VOLUME);
         g_active_direction    = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? "BUY" : "SELL";
         g_active_initial_risk = MathAbs(g_active_entry_price - g_active_entry_sl);
         break;
      }
   }

   int sh, sm, eh, em;
   string sess_desc;
   GetSessionWindow(sh, sm, eh, em, sess_desc);

   PrintFormat("=== MCP_Scalper_Sajim v1.30 INITIALIZED on %s ===", _Symbol);
   PrintFormat("Session Timing : %s", sess_desc);
   PrintFormat("Risk Model     : %s (%.1f%% per trade, fallback: %.2fL)", InpUseDynamicRisk ? "Dynamic Compounding" : "Fixed Lots", InpRiskPercent, InpLotSize);
   PrintFormat("Exit Strategy  : Option A (Two-Target Scaled Exit: 50%% @ +%.1fR + BE, Runner Trail: %s @ +%.1fR)", InpPartialClose_R, InpTrailRunner_Enable ? "ENABLED" : "OFF", InpTrailRunner_StartR);
   PrintFormat("Spread Filter  : Max %d points", g_effective_max_spread);

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                   |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(g_ema_trend_handle  != INVALID_HANDLE) IndicatorRelease(g_ema_trend_handle);
   if(g_ema_signal_handle != INVALID_HANDLE) IndicatorRelease(g_ema_signal_handle);
   if(g_rsi_handle        != INVALID_HANDLE) IndicatorRelease(g_rsi_handle);
   if(g_atr_handle        != INVALID_HANDLE) IndicatorRelease(g_atr_handle);
   if(g_htf_ema_handle    != INVALID_HANDLE) IndicatorRelease(g_htf_ema_handle);

   Print("MCP_Scalper_Sajim v1.30 removed | Reason: ", reason);
}

//+------------------------------------------------------------------+
//| Bar-by-bar Diagnostic Logger                                      |
//+------------------------------------------------------------------+
void LogBarDiagnostic(datetime bar_time, double close_1, double open_1, double high_1, double low_1,
                      double ema_t, double ema_s, double rsi_val, double atr_val,
                      double htf_val, bool htf_on, double spread_pts)
{
   string time_str = TimeToString(bar_time, TIME_MINUTES);
   
   // Check session
   bool session_open = IsWithinTradingSession();
   
   // 1. Check trends
   bool h1_bull = (!htf_on) || (close_1 > htf_val);
   bool h1_bear = (!htf_on) || (close_1 < htf_val);
   bool m5_bull = (close_1 > ema_t);
   bool m5_bear = (close_1 < ema_t);

   // 2. Pullback & candle
   bool bull_pullback_touch = (low_1 <= ema_s);
   bool bull_pullback_close = (close_1 > ema_s);
   bool bull_candle         = (close_1 > open_1);

   bool bear_pullback_touch = (high_1 >= ema_s);
   bool bear_pullback_close = (close_1 < ema_s);
   bool bear_candle         = (close_1 < open_1);

   // 3. RSI
   bool rsi_in_zone = (rsi_val >= InpRSI_Lower && rsi_val <= InpRSI_Upper);

   // Format trend context
   string h1_str = htf_on ? (close_1 > htf_val ? "H1:BULL" : "H1:BEAR") : "H1:OFF";
   string m5_str = m5_bull ? "M5:BULL" : (m5_bear ? "M5:BEAR" : "M5:FLAT");

   string diagnostic = "";

   if(!session_open)
   {
      diagnostic = "SESSION WINDOW CLOSED (Awaiting active session)";
   }
   else if(h1_bear && m5_bear)
   {
      // Bearish trend alignment
      if(!bear_pullback_touch)
         diagnostic = StringFormat("WAIT PULLBACK (High %.2f < EMA21 %.2f)", high_1, ema_s);
      else if(!bear_pullback_close)
         diagnostic = StringFormat("PULLBACK OVEREXTENDED (Close %.2f > EMA21 %.2f)", close_1, ema_s);
      else if(!bear_candle)
         diagnostic = "WAIT BEARISH CANDLE (Bar closed bullish)";
      else if(!rsi_in_zone)
         diagnostic = StringFormat("RSI OUT OF ZONE (RSI=%.1f, need %.0f-%.0f)", rsi_val, InpRSI_Lower, InpRSI_Upper);
      else if(spread_pts > g_effective_max_spread)
         diagnostic = StringFormat("SPREAD TOO HIGH (%.0f > %d pts)", spread_pts, g_effective_max_spread);
      else
         diagnostic = "ALL CONDITIONS MET -> TRIGGERING SELL";
   }
   else if(h1_bull && m5_bull)
   {
      // Bullish trend alignment
      if(!bull_pullback_touch)
         diagnostic = StringFormat("WAIT PULLBACK (Low %.2f > EMA21 %.2f)", low_1, ema_s);
      else if(!bull_pullback_close)
         diagnostic = StringFormat("PULLBACK OVEREXTENDED (Close %.2f < EMA21 %.2f)", close_1, ema_s);
      else if(!bull_candle)
         diagnostic = "WAIT BULLISH CANDLE (Bar closed bearish)";
      else if(!rsi_in_zone)
         diagnostic = StringFormat("RSI OUT OF ZONE (RSI=%.1f, need %.0f-%.0f)", rsi_val, InpRSI_Lower, InpRSI_Upper);
      else if(spread_pts > g_effective_max_spread)
         diagnostic = StringFormat("SPREAD TOO HIGH (%.0f > %d pts)", spread_pts, g_effective_max_spread);
      else
         diagnostic = "ALL CONDITIONS MET -> TRIGGERING BUY";
   }
   else
   {
      diagnostic = StringFormat("TREND CONFLICT (%s vs %s)", h1_str, m5_str);
   }

   PrintFormat("[M5 BAR %s] Price: %.2f | %s | %s | RSI: %.1f | ATR: %.2f | Spread: %.0f pts | %s",
               time_str, close_1, h1_str, m5_str, rsi_val, atr_val, spread_pts, diagnostic);
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   //--- Manage trailing stop / partial close / runner trail on every tick
   ManageTrailingStop();

   //--- New bar guard: process signal checks only once per completed bar
   datetime current_bar_time = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(current_bar_time == g_last_bar_time)
      return;

   g_last_bar_time = current_bar_time;

   //--- Reset daily trade counter at day boundary
   MqlDateTime dt;
   TimeToStruct(current_bar_time, dt);
   datetime today_date = StringToTime(StringFormat("%04d.%02d.%02d", dt.year, dt.mon, dt.day));
   if(today_date != g_last_trade_date)
   {
      g_trades_today = 0;
      g_last_trade_date = today_date;
   }

   //--- Read indicators
   double ema_trend[], ema_signal[], rsi[], atr[], htf_ema[];
   if(CopyBuffer(g_ema_trend_handle,  0, 1, 2, ema_trend)  < 2) return;
   if(CopyBuffer(g_ema_signal_handle, 0, 1, 2, ema_signal) < 2) return;
   if(CopyBuffer(g_rsi_handle,        0, 1, 1, rsi)        < 1) return;
   if(CopyBuffer(g_atr_handle,        0, 1, 1, atr)        < 1) return;

   double htf_val = 0.0;
   if(InpUseHTF_Filter)
   {
      if(CopyBuffer(g_htf_ema_handle, 0, 1, 1, htf_ema) < 1) return;
      htf_val = htf_ema[0];
   }

   // Read completed bar OHLC (bar index 1)
   double close_1 = iClose(_Symbol, PERIOD_CURRENT, 1);
   double open_1  = iOpen(_Symbol, PERIOD_CURRENT, 1);
   double high_1  = iHigh(_Symbol, PERIOD_CURRENT, 1);
   double low_1   = iLow(_Symbol, PERIOD_CURRENT, 1);
   double spread_pts = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);

   //--- Print bar diagnostic
   if(InpLogBarDiagnostics)
   {
      if(HasOpenPosition())
      {
         for(int i = PositionsTotal() - 1; i >= 0; i--)
         {
            ulong t = PositionGetTicket(i);
            if(t > 0 && PositionGetInteger(POSITION_MAGIC) == (long)InpMagicNumber && PositionGetString(POSITION_SYMBOL) == _Symbol)
            {
               double p_profit = PositionGetDouble(POSITION_PROFIT);
               double p_open   = PositionGetDouble(POSITION_PRICE_OPEN);
               double p_vol    = PositionGetDouble(POSITION_VOLUME);
               double p_sl     = PositionGetDouble(POSITION_SL);
               double p_tp     = PositionGetDouble(POSITION_TP);
               string dir      = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? "BUY" : "SELL";
               PrintFormat("[M5 BAR %s] IN POSITION #%d | %s %.2f @ %.2f | SL: %.2f | TP: %.2f | Floating: $%.2f",
                           TimeToString(current_bar_time, TIME_MINUTES), t, dir, p_vol, p_open, p_sl, p_tp, p_profit);
               break;
            }
         }
      }
      else
      {
         LogBarDiagnostic(current_bar_time, close_1, open_1, high_1, low_1,
                           ema_trend[0], ema_signal[0], rsi[0], atr[0],
                           htf_val, InpUseHTF_Filter, spread_pts);
      }
   }

   //--- FILTER: Session timing window
   if(!IsWithinTradingSession())
      return;

   //--- FILTER: Max trades per day
   if(InpMaxTradesPerDay > 0 && g_trades_today >= InpMaxTradesPerDay)
      return;

   //--- FILTER: Spread check
   if(spread_pts > g_effective_max_spread)
      return;

   //--- FILTER: Already have open position
   if(HasOpenPosition())
      return;

   //--- ATR-based SL/TP calculation
   double atr_val = atr[0];
   if(atr_val <= 0) return;

   double sl_distance = atr_val * InpSL_ATR_Mult;
   double tp_distance = atr_val * InpTP_ATR_Mult;

   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick_size <= 0) tick_size = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   sl_distance = MathRound(sl_distance / tick_size) * tick_size;
   tp_distance = MathRound(tp_distance / tick_size) * tick_size;

   // Calculate Dynamic Position Size
   double trade_lots = CalculatePositionSize(sl_distance);

   //--- SIGNAL: BUY (Long)
   bool htf_bullish = (!InpUseHTF_Filter) || (close_1 > htf_ema[0]);
   if(htf_bullish &&
      close_1 > ema_trend[0] &&
      low_1 <= ema_signal[0] && close_1 > ema_signal[0] &&
      close_1 > open_1 &&
      rsi[0] >= InpRSI_Lower && rsi[0] <= InpRSI_Upper)
   {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl  = NormalizeDouble(ask - sl_distance, _Digits);
      double tp  = NormalizeDouble(ask + tp_distance, _Digits);

      if(g_trade.Buy(trade_lots, _Symbol, ask, sl, tp, "MCP_Scalp_BUY"))
      {
         g_trades_today++;
         g_partial_done_ticket = 0;
         g_runner_unlocked_tp  = false;
         ulong ticket = g_trade.ResultOrder();

         // Store forensic metadata
         g_active_ticket       = ticket;
         g_active_entry_time   = TimeCurrent();
         g_active_entry_price  = ask;
         g_active_entry_sl     = sl;
         g_active_entry_tp     = tp;
         g_active_entry_rsi    = rsi[0];
         g_active_entry_atr    = atr_val;
         g_active_initial_risk = MathAbs(ask - sl);
         g_active_initial_lots = trade_lots;
         g_active_direction    = "BUY";

         PrintFormat("[ENTRY FIRED] BUY Ticket #%d | Lots: %.2f @ %.2f | SL: %.2f (1.0R) | TP: %.2f (3.0R) | RSI: %.1f | ATR: %.2f | Risk: %.1f%%",
                     ticket, trade_lots, ask, sl, tp, rsi[0], atr_val, InpUseDynamicRisk ? InpRiskPercent : 0.0);

         AppendJournalCsv("ENTRY", ticket, "BUY", trade_lots, ask, sl, tp,
                          rsi[0], atr_val, spread_pts, 0.0, 0.0,
                          StringFormat("RSI=%.1f ATR=%.2f Pullback Rejection", rsi[0], atr_val));
      }
      return;
   }

   //--- SIGNAL: SELL (Short)
   bool htf_bearish = (!InpUseHTF_Filter) || (close_1 < htf_ema[0]);
   if(htf_bearish &&
      close_1 < ema_trend[0] &&
      high_1 >= ema_signal[0] && close_1 < ema_signal[0] &&
      close_1 < open_1 &&
      rsi[0] >= InpRSI_Lower && rsi[0] <= InpRSI_Upper)
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl  = NormalizeDouble(bid + sl_distance, _Digits);
      double tp  = NormalizeDouble(bid - tp_distance, _Digits);

      if(g_trade.Sell(trade_lots, _Symbol, bid, sl, tp, "MCP_Scalp_SELL"))
      {
         g_trades_today++;
         g_partial_done_ticket = 0;
         g_runner_unlocked_tp  = false;
         ulong ticket = g_trade.ResultOrder();

         // Store forensic metadata
         g_active_ticket       = ticket;
         g_active_entry_time   = TimeCurrent();
         g_active_entry_price  = bid;
         g_active_entry_sl     = sl;
         g_active_entry_tp     = tp;
         g_active_entry_rsi    = rsi[0];
         g_active_entry_atr    = atr_val;
         g_active_initial_risk = MathAbs(sl - bid);
         g_active_initial_lots = trade_lots;
         g_active_direction    = "SELL";

         PrintFormat("[ENTRY FIRED] SELL Ticket #%d | Lots: %.2f @ %.2f | SL: %.2f (1.0R) | TP: %.2f (3.0R) | RSI: %.1f | ATR: %.2f | Risk: %.1f%%",
                     ticket, trade_lots, bid, sl, tp, rsi[0], atr_val, InpUseDynamicRisk ? InpRiskPercent : 0.0);

         AppendJournalCsv("ENTRY", ticket, "SELL", trade_lots, bid, sl, tp,
                          rsi[0], atr_val, spread_pts, 0.0, 0.0,
                          StringFormat("RSI=%.1f ATR=%.2f Pullback Rejection", rsi[0], atr_val));
      }
      return;
   }
}

//+------------------------------------------------------------------+
//| Check if we have an open position with our magic number           |
//+------------------------------------------------------------------+
bool HasOpenPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) == (long)InpMagicNumber &&
         PositionGetString(POSITION_SYMBOL) == _Symbol)
         return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Calculate minimum lot size allowed by broker                      |
//+------------------------------------------------------------------+
double GetMinLot()
{
   return SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
}

//+------------------------------------------------------------------+
//| Manage Breakeven, Partial Close, and Dynamic Trailing Runner      |
//+------------------------------------------------------------------+
void ManageTrailingStop()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      double open_price  = PositionGetDouble(POSITION_PRICE_OPEN);
      double current_sl  = PositionGetDouble(POSITION_SL);
      double current_tp  = PositionGetDouble(POSITION_TP);
      double current_vol = PositionGetDouble(POSITION_VOLUME);
      long   pos_type    = PositionGetInteger(POSITION_TYPE);

      double atr[];
      if(CopyBuffer(g_atr_handle, 0, 0, 1, atr) < 1) return;
      double atr_val = atr[0];
      if(atr_val <= 0) return;

      double risk_dist       = atr_val * InpSL_ATR_Mult;
      double be_trigger_dist = risk_dist * InpBreakeven_R;
      double partial_trigger = risk_dist * InpPartialClose_R;
      double runner_trigger  = risk_dist * InpTrailRunner_StartR;
      double be_offset       = InpBE_Buffer_Points * _Point;
      double min_lot         = GetMinLot();
      double spread_pts      = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);

      if(pos_type == POSITION_TYPE_BUY)
      {
         double bid         = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         double profit_dist = bid - open_price;

         //--- STEP 1: Partial Close at +1.5R (Lock 50% cash)
         if(InpPartialClose_Enable && ticket != g_partial_done_ticket &&
            profit_dist >= partial_trigger && current_vol > min_lot)
         {
            double close_lots = NormalizeDouble(current_vol * InpPartialClose_Pct / 100.0, 2);
            close_lots        = MathFloor(close_lots / min_lot) * min_lot;
            if(close_lots < min_lot) close_lots = min_lot;
            if(close_lots >= current_vol) close_lots = NormalizeDouble(current_vol / 2.0, 2);

            if(close_lots >= min_lot && close_lots < current_vol)
            {
               if(g_trade.PositionClosePartial(ticket, close_lots))
               {
                  g_partial_done_ticket = ticket;
                  double partial_pnl = close_lots * (bid - open_price) * 100.0;
                  PrintFormat("[PARTIAL CLOSE] BUY Ticket #%d | Closed %.2f lots @ %.2f (+%.1fR) | Remaining: %.2f lots",
                              ticket, close_lots, bid, InpPartialClose_R, current_vol - close_lots);

                  AppendJournalCsv("PARTIAL_CLOSE", ticket, "BUY", close_lots, bid,
                                   current_sl, current_tp, 0.0, atr_val, spread_pts,
                                   partial_pnl, InpPartialClose_R, "50% locked at +1.5R");
               }
            }
         }

         //--- STEP 2: Move SL to Breakeven
         if(InpBreakeven_R > 0 && profit_dist >= be_trigger_dist)
         {
            double be_sl = NormalizeDouble(open_price + be_offset, _Digits);
            if(be_sl > current_sl + _Point)
            {
               if(g_trade.PositionModify(ticket, be_sl, current_tp))
               {
                  PrintFormat("[BREAKEVEN SL] BUY Ticket #%d | SL locked at %.2f (+BE buffer)", ticket, be_sl);
                  AppendJournalCsv("BREAKEVEN_SL", ticket, "BUY", current_vol, bid,
                                   be_sl, current_tp, 0.0, atr_val, spread_pts,
                                   0.0, InpBreakeven_R, "Risk removed; SL at BE");
                  current_sl = be_sl;
               }
            }
         }

         //--- STEP 3: Dynamic Trailing Runner (Post +2.0R to catch +5R to +8R home runs)
         if(InpTrailRunner_Enable && profit_dist >= runner_trigger)
         {
            double trail_dist = atr_val * InpTrailRunner_ATR;
            double tick_size  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
            if(tick_size <= 0) tick_size = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
            trail_dist = MathRound(trail_dist / tick_size) * tick_size;

            double new_sl = NormalizeDouble(bid - trail_dist, _Digits);
            if(new_sl > current_sl + _Point)
            {
               // If runner passed +2.0R, remove the hard TP cap so it can run freely!
               double target_tp = current_tp;
               if(!g_runner_unlocked_tp && current_tp > 0)
               {
                  target_tp = 0.0; // Remove TP cap for unlimited home-run expansion
                  g_runner_unlocked_tp = true;
                  PrintFormat("[RUNNER UNLOCKED] BUY Ticket #%d crossed +%.1fR -> TP cap removed, trailing ATR activated!", ticket, InpTrailRunner_StartR);
               }

               if(g_trade.PositionModify(ticket, new_sl, target_tp))
               {
                  current_sl = new_sl;
                  PrintFormat("[RUNNER TRAIL] BUY Ticket #%d | SL trailed up to %.2f", ticket, new_sl);
               }
            }
         }
      }
      else if(pos_type == POSITION_TYPE_SELL)
      {
         double ask         = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         double profit_dist = open_price - ask;

         //--- STEP 1: Partial Close at +1.5R (Lock 50% cash)
         if(InpPartialClose_Enable && ticket != g_partial_done_ticket &&
            profit_dist >= partial_trigger && current_vol > min_lot)
         {
            double close_lots = NormalizeDouble(current_vol * InpPartialClose_Pct / 100.0, 2);
            close_lots        = MathFloor(close_lots / min_lot) * min_lot;
            if(close_lots < min_lot) close_lots = min_lot;
            if(close_lots >= current_vol) close_lots = NormalizeDouble(current_vol / 2.0, 2);

            if(close_lots >= min_lot && close_lots < current_vol)
            {
               if(g_trade.PositionClosePartial(ticket, close_lots))
               {
                  g_partial_done_ticket = ticket;
                  double partial_pnl = close_lots * (open_price - ask) * 100.0;
                  PrintFormat("[PARTIAL CLOSE] SELL Ticket #%d | Closed %.2f lots @ %.2f (+%.1fR) | Remaining: %.2f lots",
                              ticket, close_lots, ask, InpPartialClose_R, current_vol - close_lots);

                  AppendJournalCsv("PARTIAL_CLOSE", ticket, "SELL", close_lots, ask,
                                   current_sl, current_tp, 0.0, atr_val, spread_pts,
                                   partial_pnl, InpPartialClose_R, "50% locked at +1.5R");
               }
            }
         }

         //--- STEP 2: Move SL to Breakeven
         if(InpBreakeven_R > 0 && profit_dist >= be_trigger_dist)
         {
            double be_sl = NormalizeDouble(open_price - be_offset, _Digits);
            if(be_sl < current_sl - _Point || current_sl == 0)
            {
               if(g_trade.PositionModify(ticket, be_sl, current_tp))
               {
                  PrintFormat("[BREAKEVEN SL] SELL Ticket #%d | SL locked at %.2f (+BE buffer)", ticket, be_sl);
                  AppendJournalCsv("BREAKEVEN_SL", ticket, "SELL", current_vol, ask,
                                   be_sl, current_tp, 0.0, atr_val, spread_pts,
                                   0.0, InpBreakeven_R, "Risk removed; SL at BE");
                  current_sl = be_sl;
               }
            }
         }

         //--- STEP 3: Dynamic Trailing Runner (Post +2.0R to catch +5R to +8R home runs)
         if(InpTrailRunner_Enable && profit_dist >= runner_trigger)
         {
            double trail_dist = atr_val * InpTrailRunner_ATR;
            double tick_size  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
            if(tick_size <= 0) tick_size = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
            trail_dist = MathRound(trail_dist / tick_size) * tick_size;

            double new_sl = NormalizeDouble(ask + trail_dist, _Digits);
            if(new_sl < current_sl - _Point || current_sl == 0)
            {
               // If runner passed +2.0R, remove the hard TP cap so it can run freely!
               double target_tp = current_tp;
               if(!g_runner_unlocked_tp && current_tp > 0)
               {
                  target_tp = 0.0; // Remove TP cap for unlimited home-run expansion
                  g_runner_unlocked_tp = true;
                  PrintFormat("[RUNNER UNLOCKED] SELL Ticket #%d crossed +%.1fR -> TP cap removed, trailing ATR activated!", ticket, InpTrailRunner_StartR);
               }

               if(g_trade.PositionModify(ticket, new_sl, target_tp))
               {
                  current_sl = new_sl;
                  PrintFormat("[RUNNER TRAIL] SELL Ticket #%d | SL trailed down to %.2f", ticket, new_sl);
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Trade Transaction Handler: Detailed Forensic Capture on Deal Close|
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction& trans,
                        const MqlTradeRequest& request,
                        const MqlTradeResult& result)
{
   // Listen for Deal Additions (executions)
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
   {
      ulong deal_ticket = trans.deal;
      if(deal_ticket == 0) return;

      if(HistoryDealSelect(deal_ticket))
      {
         long deal_magic  = HistoryDealGetInteger(deal_ticket, DEAL_MAGIC);
         string deal_sym  = HistoryDealGetString(deal_ticket, DEAL_SYMBOL);

         if(deal_magic == (long)InpMagicNumber && deal_sym == _Symbol)
         {
            long   entry_type  = HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);
            double deal_profit = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
            double deal_volume = HistoryDealGetDouble(deal_ticket, DEAL_VOLUME);
            double deal_price  = HistoryDealGetDouble(deal_ticket, DEAL_PRICE);
            string deal_comm   = HistoryDealGetString(deal_ticket, DEAL_COMMENT);
            ulong  order_id    = HistoryDealGetInteger(deal_ticket, DEAL_ORDER);
            long   deal_type   = HistoryDealGetInteger(deal_ticket, DEAL_TYPE);
            string dir_str     = (deal_type == DEAL_TYPE_BUY) ? "BUY" : "SELL";

            // If exit deal
            if(entry_type == DEAL_ENTRY_OUT || entry_type == DEAL_ENTRY_OUT_BY)
            {
               double r_ret = 0.0;
               if(g_active_initial_risk > 0.0)
               {
                  double price_move = (g_active_direction == "BUY") ? (deal_price - g_active_entry_price) : (g_active_entry_price - deal_price);
                  r_ret = price_move / g_active_initial_risk;
               }

               string exit_type = "EXIT_MANUAL";
               if(StringFind(deal_comm, "tp") >= 0) exit_type = "TAKE_PROFIT";
               else if(StringFind(deal_comm, "sl") >= 0)
               {
                  if(deal_profit >= -1.0 && deal_profit <= 2.0)
                     exit_type = "BREAKEVEN_EXIT";
                  else
                     exit_type = "STOP_LOSS";
               }

               PrintFormat("[FORENSIC EXIT] Ticket #%d | %s | PnL: $%.2f | R: %+.2fR | ExitPrice: %.2f | Comment: %s",
                           order_id, exit_type, deal_profit, r_ret, deal_price, deal_comm);

               AppendJournalCsv(exit_type, order_id, dir_str, deal_volume, deal_price,
                                0.0, 0.0, 0.0, 0.0, 0.0, deal_profit, r_ret, deal_comm);

               // If no more open positions, reset active forensic tracker
               if(!HasOpenPosition())
               {
                  g_active_ticket = 0;
                  g_active_initial_risk = 0.0;
                  g_runner_unlocked_tp = false;
               }
            }
         }
      }
   }
}
//+------------------------------------------------------------------+
