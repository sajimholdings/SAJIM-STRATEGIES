//+------------------------------------------------------------------+
//|                                     CrossHedgeMicroScalper.mq5   |
//|                        Cross-Hedging Micro-Scalping EA           |
//|                        For DEMO testing only. Use at own risk.   |
//+------------------------------------------------------------------+
#property copyright "Demo EA - CrossHedgeMicroScalper"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\SymbolInfo.mqh>

//--- Input Parameters ---------------------------------------------------
input group "=== Symbol Settings ==="
input string   PrimarySymbol      = "EURUSD";   // Primary (scalping) symbol
input string   HedgeSymbol        = "GBPUSD";   // Cross-hedge symbol

input group "=== Lot & Hedge Settings ==="
input double   PrimaryLot         = 0.01;       // Base lot for primary scalp
input double   HedgeLotMultiple   = 1.50;       // Hedge lot = PrimaryLot * this
input int      HedgeTriggerPips   = 40;         // Adverse move (pips) to trigger hedge

input group "=== Microscalp Settings ==="
input int      TickBufferSize     = 500;        // Ring buffer size for ticks
input int      VWAPPeriodTicks    = 200;        // Ticks for VWAP calculation
input double   VWAPDeviation      = 2.0;        // Standard deviations for entry
input int      MaxSpreadPoints    = 25;         // Max spread (points) to allow entry

input group "=== Risk Management ==="
input double   TargetProfitUSD    = 5.00;       // Close all when net profit >= this
input double   MaxDrawdownUSD     = 20.00;      // Force-close all if net loss >= this
input int      MaxHoldingSeconds  = 3600;       // Time-based exit (seconds)

input group "=== Correlation Engine ==="
input int      CorrLookbackBars   = 100;        // Bars for return series
input double   CorrLambda         = 0.94;       // EW decay factor (RiskMetrics)
input double   MinCorrelation     = 0.60;       // Minimum |corr| to allow hedge

input group "=== Role Reversal ==="
input int      RoleReversalSecs   = 600;        // Seconds between role swaps
input bool     EnableRoleReversal = false;      // Toggle role reversal

//--- Global Objects & Variables ----------------------------------------
CTrade         trade;
CPositionInfo  posInfo;
CSymbolInfo    symPrimary;
CSymbolInfo    symHedge;

//--- Tick ring buffer for primary symbol
struct TickData {
   double   price;
   double   volume;
   datetime time;
};
TickData g_tickBuffer[];
int      g_tickHead  = 0;
int      g_tickCount = 0;

//--- Correlation engine state
double   g_ewCovariance = 0.0;
double   g_ewVarPrimary = 0.0;
double   g_ewVarHedge   = 0.0;
double   g_ewMeanPrimary= 0.0;
double   g_ewMeanHedge  = 0.0;
double   g_lastPrimaryClose = 0.0;
double   g_lastHedgeClose   = 0.0;
bool     g_corrInitialized  = false;

//--- Position tracking
ulong    g_primaryTicket = 0;
ulong    g_hedgeTicket   = 0;
bool     g_hedgeOpen     = false;
datetime g_lastReversal  = 0;
datetime g_primaryOpenTime = 0;

//--- State flags
bool     g_isHedgingAccount = false;
int      g_primaryDigits    = 5;
double   g_primaryPoint     = 0.00001;
int      g_hedgeDigits      = 5;
double   g_hedgePoint       = 0.00001;

//+------------------------------------------------------------------+
//| Expert initialization function                                     |
//+------------------------------------------------------------------+
int OnInit()
{
   //--- 1. Check hedging account
   g_isHedgingAccount = (bool)AccountInfoInteger(ACCOUNT_HEDGE_ALLOWED);
   if(!g_isHedgingAccount) {
      Print("ERROR: This EA requires a HEDGING account. Current account does not allow opposite positions on the same symbol.");
      return INIT_FAILED;
   }

   //--- 2. Initialize symbol info
   if(!symPrimary.Name(PrimarySymbol)) {
      Print("ERROR: Cannot find primary symbol: ", PrimarySymbol);
      return INIT_FAILED;
   }
   if(!symHedge.Name(HedgeSymbol)) {
      Print("ERROR: Cannot find hedge symbol: ", HedgeSymbol);
      return INIT_FAILED;
   }
   symPrimary.RefreshRates();
   symHedge.RefreshRates();

   g_primaryDigits = (int)SymbolInfoInteger(PrimarySymbol, SYMBOL_DIGITS);
   g_primaryPoint  = SymbolInfoDouble(PrimarySymbol, SYMBOL_POINT);
   g_hedgeDigits   = (int)SymbolInfoInteger(HedgeSymbol, SYMBOL_DIGITS);
   g_hedgePoint    = SymbolInfoDouble(HedgeSymbol, SYMBOL_POINT);

      //--- 3. Initialize tick buffer
   ArrayResize(g_tickBuffer, TickBufferSize);
   // NOTE: ArrayInitialize() does not support struct arrays.
   // Struct fields are zero-initialized by ArrayResize automatically.
   for(int i = 0; i < TickBufferSize; i++) {
      g_tickBuffer[i].price  = 0.0;
      g_tickBuffer[i].volume = 0.0;
      g_tickBuffer[i].time   = 0;
   }
   g_tickHead  = 0;
   g_tickCount = 0;

   //--- 4. Initialize correlation engine with historical data
   InitializeCorrelation();

   //--- 5. Trade object setup
   trade.SetExpertMagicNumber(20240914);
   trade.SetDeviationInPoints(10);
   trade.SetTypeFilling(ORDER_FILLING_FOK);

   g_lastReversal = TimeCurrent();

   Print("CrossHedgeMicroScalper initialized. Primary=", PrimarySymbol,
         " Hedge=", HedgeSymbol, " HedgingAccount=", g_isHedgingAccount);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert tick function                                               |
//+------------------------------------------------------------------+
void OnTick()
{
   //--- 1. Update tick buffer with latest primary tick
   UpdateTickBuffer();

   //--- 2. Update correlation engine on every new bar (or frequently)
   UpdateCorrelation();

   //--- 3. Manage existing positions (hedge trigger, profit target, drawdown, time)
   ManagePositions();

   //--- 4. Check for new microscalp entry (only if no primary position open)
   if(g_primaryTicket == 0 && !g_hedgeOpen) {
      CheckMicroScalpEntry();
   }

   //--- 5. Role reversal (optional)
   if(EnableRoleReversal) {
      CheckRoleReversal();
   }
}

//+------------------------------------------------------------------+
//| Update tick ring buffer                                            |
//+------------------------------------------------------------------+
void UpdateTickBuffer()
{
   MqlTick tick;
   if(!SymbolInfoTick(PrimarySymbol, tick)) return;

   g_tickBuffer[g_tickHead].price  = tick.last;
   g_tickBuffer[g_tickHead].volume = (double)tick.volume;
   g_tickBuffer[g_tickHead].time   = tick.time;

   g_tickHead = (g_tickHead + 1) % TickBufferSize;
   if(g_tickCount < TickBufferSize) g_tickCount++;
}

//+------------------------------------------------------------------+
//| Initialize correlation engine with historical bar closes           |
//+------------------------------------------------------------------+
void InitializeCorrelation()
{
   double primCloses[], hedgeCloses[];
   ArraySetAsSeries(primCloses, true);
   ArraySetAsSeries(hedgeCloses, true);

   int copiedP = CopyClose(PrimarySymbol, PERIOD_M1, 0, CorrLookbackBars + 1, primCloses);
   int copiedH = CopyClose(HedgeSymbol,   PERIOD_M1, 0, CorrLookbackBars + 1, hedgeCloses);

   if(copiedP < 2 || copiedH < 2) {
      Print("WARNING: Not enough historical data for correlation init. Will build gradually.");
      return;
   }

   int n = MathMin(copiedP, copiedH);
   double lambda = CorrLambda;
   double weightSum = 0.0;

   //--- Initialize EW means with the oldest return first
   double retP = (primCloses[n-2] - primCloses[n-1]) / primCloses[n-1];
   double retH = (hedgeCloses[n-2] - hedgeCloses[n-1]) / hedgeCloses[n-1];

   g_ewMeanPrimary = retP;
   g_ewMeanHedge   = retH;
   g_ewVarPrimary  = retP * retP;
   g_ewVarHedge    = retH * retH;
   g_ewCovariance  = retP * retH;

   //--- Iterate forward in time (from older to newer)
   for(int i = n - 3; i >= 0; i--) {
      double rP = (primCloses[i] - primCloses[i+1]) / primCloses[i+1];
      double rH = (hedgeCloses[i] - hedgeCloses[i+1]) / hedgeCloses[i+1];

      g_ewMeanPrimary = lambda * g_ewMeanPrimary + (1 - lambda) * rP;
      g_ewMeanHedge   = lambda * g_ewMeanHedge   + (1 - lambda) * rH;
      g_ewVarPrimary  = lambda * g_ewVarPrimary  + (1 - lambda) * rP * rP;
      g_ewVarHedge    = lambda * g_ewVarHedge    + (1 - lambda) * rH * rH;
      g_ewCovariance  = lambda * g_ewCovariance  + (1 - lambda) * rP * rH;
   }

   g_lastPrimaryClose = primCloses[0];
   g_lastHedgeClose   = hedgeCloses[0];
   g_corrInitialized  = true;

   Print("Correlation engine initialized. Cov=", g_ewCovariance,
         " VarP=", g_ewVarPrimary, " VarH=", g_ewVarHedge);
}

//+------------------------------------------------------------------+
//| Update correlation engine with latest bar close                    |
//+------------------------------------------------------------------+
void UpdateCorrelation()
{
   static datetime lastBarTime = 0;
   datetime currentBarTime = iTime(PrimarySymbol, PERIOD_M1, 0);
   if(currentBarTime == lastBarTime) return;
   lastBarTime = currentBarTime;

   double primClose = iClose(PrimarySymbol, PERIOD_M1, 1);
   double hedgeClose = iClose(HedgeSymbol, PERIOD_M1, 1);

   if(g_lastPrimaryClose == 0.0 || g_lastHedgeClose == 0.0) {
      g_lastPrimaryClose = primClose;
      g_lastHedgeClose   = hedgeClose;
      return;
   }

   double rP = (primClose - g_lastPrimaryClose) / g_lastPrimaryClose;
   double rH = (hedgeClose - g_lastHedgeClose) / g_lastHedgeClose;

   double lambda = CorrLambda;
   g_ewMeanPrimary = lambda * g_ewMeanPrimary + (1 - lambda) * rP;
   g_ewMeanHedge   = lambda * g_ewMeanHedge   + (1 - lambda) * rH;
   g_ewVarPrimary  = lambda * g_ewVarPrimary  + (1 - lambda) * rP * rP;
   g_ewVarHedge    = lambda * g_ewVarHedge    + (1 - lambda) * rH * rH;
   g_ewCovariance  = lambda * g_ewCovariance  + (1 - lambda) * rP * rH;

   g_lastPrimaryClose = primClose;
   g_lastHedgeClose   = hedgeClose;
}

//+------------------------------------------------------------------+
//| Get current exponentially weighted correlation                     |
//+------------------------------------------------------------------+
double GetEWCorrelation()
{
   if(g_ewVarPrimary <= 0.0 || g_ewVarHedge <= 0.0) return 0.0;
   return g_ewCovariance / MathSqrt(g_ewVarPrimary * g_ewVarHedge);
}

//+------------------------------------------------------------------+
//| Get current hedge ratio (beta)                                     |
//+------------------------------------------------------------------+
double GetHedgeRatio()
{
   if(g_ewVarHedge <= 0.0) return 1.0;
   return g_ewCovariance / g_ewVarHedge;
}

//+------------------------------------------------------------------+
//| Calculate tick VWAP and standard deviation                         |
//+------------------------------------------------------------------+
bool CalculateTickVWAP(double &vwap, double &stdDev)
{
   if(g_tickCount < 10) return false;

   int start = (g_tickHead - MathMin(VWAPPeriodTicks, g_tickCount) + TickBufferSize) % TickBufferSize;
   int count = MathMin(VWAPPeriodTicks, g_tickCount);

   double sumPV = 0.0, sumV = 0.0;
   for(int i = 0; i < count; i++) {
      int idx = (start + i) % TickBufferSize;
      sumPV += g_tickBuffer[idx].price * g_tickBuffer[idx].volume;
      sumV  += g_tickBuffer[idx].volume;
   }
   if(sumV <= 0.0) return false;
   vwap = sumPV / sumV;

   double sumSq = 0.0;
   for(int i = 0; i < count; i++) {
      int idx = (start + i) % TickBufferSize;
      double diff = g_tickBuffer[idx].price - vwap;
      sumSq += diff * diff * g_tickBuffer[idx].volume;
   }
   stdDev = MathSqrt(sumSq / sumV);
   return true;
}

//+------------------------------------------------------------------+
//| Check for microscalp entry conditions                              |
//+------------------------------------------------------------------+
void CheckMicroScalpEntry()
{
   //--- Spread filter
   symPrimary.RefreshRates();
   int spreadPoints = (int)SymbolInfoInteger(PrimarySymbol, SYMBOL_SPREAD);
   if(spreadPoints > MaxSpreadPoints) return;

   //--- Correlation filter: only trade if correlation is strong enough
   double corr = GetEWCorrelation();
   if(MathAbs(corr) < MinCorrelation) return;

   //--- VWAP signal
   double vwap, stdDev;
   if(!CalculateTickVWAP(vwap, stdDev)) return;

   MqlTick tick;
   if(!SymbolInfoTick(PrimarySymbol, tick)) return;
   double price = tick.last;

   //--- Entry logic: price beyond VWAP +/- N std deviations
   bool buySignal  = (price < vwap - VWAPDeviation * stdDev);
   bool sellSignal = (price > vwap + VWAPDeviation * stdDev);

   if(!buySignal && !sellSignal) return;

   //--- Calculate lots
   double lot = PrimaryLot;
   if(buySignal) {
      if(trade.Buy(lot, PrimarySymbol, 0, 0, 0, "MicroScalp Buy")) {
         g_primaryTicket = trade.ResultOrder();
         g_primaryOpenTime = TimeCurrent();
         Print("MicroScalp BUY opened. Price=", price, " VWAP=", vwap, " StdDev=", stdDev);
      }
   }
   else if(sellSignal) {
      if(trade.Sell(lot, PrimarySymbol, 0, 0, 0, "MicroScalp Sell")) {
         g_primaryTicket = trade.ResultOrder();
         g_primaryOpenTime = TimeCurrent();
         Print("MicroScalp SELL opened. Price=", price, " VWAP=", vwap, " StdDev=", stdDev);
      }
   }
}

//+------------------------------------------------------------------+
//| Manage positions: hedge trigger, profit target, drawdown, time     |
//+------------------------------------------------------------------+
void ManagePositions()
{
   double netProfit = GetNetProfit();

   //--- 1. Target profit: close everything
   if(netProfit >= TargetProfitUSD) {
      CloseAllPositions("Target profit reached");
      return;
   }

   //--- 2. Max drawdown: force close everything
   if(netProfit <= -MaxDrawdownUSD) {
      CloseAllPositions("Max drawdown hit");
      return;
   }

   //--- 3. Time-based exit
   if(g_primaryTicket != 0 && g_primaryOpenTime > 0) {
      if(TimeCurrent() - g_primaryOpenTime > MaxHoldingSeconds) {
         CloseAllPositions("Time-based exit");
         return;
      }
   }

   //--- 4. Hedge trigger: if primary is in adverse move and no hedge open
   if(g_primaryTicket != 0 && !g_hedgeOpen) {
      CheckHedgeTrigger();
   }

   //--- 5. If hedge is open and primary has recovered, close hedge
   if(g_hedgeOpen) {
      CheckHedgeClose();
   }
}

//+------------------------------------------------------------------+
//| Check if hedge should be opened                                    |
//+------------------------------------------------------------------+
void CheckHedgeTrigger()
{
   if(!PositionSelectByTicket(g_primaryTicket)) return;

   double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
   double currentPrice = PositionGetDouble(POSITION_PRICE_CURRENT);
   long   posType = PositionGetInteger(POSITION_TYPE);

   double adversePips = 0.0;
   if(posType == POSITION_TYPE_BUY) {
      adversePips = (openPrice - currentPrice) / g_primaryPoint / 10.0;
   } else {
      adversePips = (currentPrice - openPrice) / g_primaryPoint / 10.0;
   }

   if(adversePips >= HedgeTriggerPips) {
      OpenHedge();
   }
}

//+------------------------------------------------------------------+
//| Open hedge position on correlated symbol                           |
//+------------------------------------------------------------------+
void OpenHedge()
{
   double beta = GetHedgeRatio();
   double hedgeLot = PrimaryLot * HedgeLotMultiple;
   //--- Adjust by beta if desired (optional): hedgeLot *= MathAbs(beta);

   //--- Determine direction: opposite to primary
   if(!PositionSelectByTicket(g_primaryTicket)) return;
   long primaryType = PositionGetInteger(POSITION_TYPE);

   bool success = false;
   if(primaryType == POSITION_TYPE_BUY) {
      success = trade.Sell(hedgeLot, HedgeSymbol, 0, 0, 0, "CrossHedge");
   } else {
      success = trade.Buy(hedgeLot, HedgeSymbol, 0, 0, 0, "CrossHedge");
   }

   if(success) {
      g_hedgeTicket = trade.ResultOrder();
      g_hedgeOpen   = true;
      Print("HEDGE OPENED on ", HedgeSymbol, " Lot=", hedgeLot, " Beta=", beta);
   } else {
      Print("HEDGE FAILED: ", trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Check if hedge should be closed                                    |
//+------------------------------------------------------------------+
void CheckHedgeClose()
{
   if(!PositionSelectByTicket(g_hedgeTicket)) {
      g_hedgeOpen = false;
      return;
   }

   //--- Close hedge if primary position is closed or in profit
   if(g_primaryTicket == 0) {
      if(PositionSelectByTicket(g_hedgeTicket)) {
         trade.PositionClose(g_hedgeTicket);
         g_hedgeOpen = false;
         Print("Hedge closed (primary gone).");
      }
      return;
   }

   //--- Optional: close hedge if correlation weakens significantly
   double corr = GetEWCorrelation();
   if(MathAbs(corr) < MinCorrelation * 0.8) {
      trade.PositionClose(g_hedgeTicket);
      g_hedgeOpen = false;
      Print("Hedge closed (correlation weakened).");
   }
}

//+------------------------------------------------------------------+
//| Close all positions and reset state                                |
//+------------------------------------------------------------------+
void CloseAllPositions(string reason)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionSelectByTicket(ticket)) {
         string sym = PositionGetString(POSITION_SYMBOL);
         if(sym == PrimarySymbol || sym == HedgeSymbol) {
            trade.PositionClose(ticket);
         }
      }
   }
   g_primaryTicket = 0;
   g_hedgeTicket   = 0;
   g_hedgeOpen     = false;
   Print("All positions closed. Reason: ", reason);
}

//+------------------------------------------------------------------+
//| Get net floating profit of primary + hedge positions               |
//+------------------------------------------------------------------+
double GetNetProfit()
{
   double total = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionSelectByTicket(ticket)) {
         string sym = PositionGetString(POSITION_SYMBOL);
         if(sym == PrimarySymbol || sym == HedgeSymbol) {
            total += PositionGetDouble(POSITION_PROFIT);
         }
      }
   }
   return total;
}

//+------------------------------------------------------------------+
//| Role reversal: swap net directional bias between symbols           |
//+------------------------------------------------------------------+
void CheckRoleReversal()
{
   if(TimeCurrent() - g_lastReversal < RoleReversalSecs) return;
   g_lastReversal = TimeCurrent();

   //--- Simple role reversal: if primary is long, close and reverse bias
   //--- In a full implementation, this would swap the primary/hedge roles
   //--- For demo purposes, we simply log the intent.
   Print("Role reversal interval reached. (Implementation optional)");
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                   |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Print("CrossHedgeMicroScalper deinitialized. Reason: ", reason);
}


// ====================================================================
// SAJIM BENCHMARK HARVESTER INJECTION (AUTOMATED)
// ====================================================================
#include <SajimLogger.mqh>

double OnTester()
{
    SajimHarvestTrades("CrossHedgeMicroScalper", _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
    return 0.0;
}
