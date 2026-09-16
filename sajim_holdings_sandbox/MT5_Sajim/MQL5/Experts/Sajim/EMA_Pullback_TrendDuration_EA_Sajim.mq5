//+------------------------------------------------------------------+
//|                              EMA_Pullback_TrendDuration_EA.mq5   |
//| Combined: EMA Pullback System + Trend Duration Forecast +        |
//|           RSI Divergence entry filter + signal alerts            |
//|                                                                   |
//| Designed to run on H1 (H4 used for bias). M15 works too but      |
//| strategy behaviour changes — the H4 bias filter is unchanged.    |
//+------------------------------------------------------------------+
#property copyright "Combined EA"
#property version   "2.00"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//======================================================================
// INPUTS
//======================================================================
input group "EMA Pullback System"
input double riskPercent    = 0.5;    // Risk % per trade
input double rrRatio        = 2.0;    // Reward:Risk ratio
input int    swingLen       = 8;      // Swing Lookback (bars each side)
input double minRoomRatio   = 0.8;    // Min Room-to-Target Ratio
input bool   useRoomFilter  = true;   // Use Room-to-Target Filter
input bool   closeByFriday  = true;   // Force close by Friday
input int    fridayHour     = 20;     // Friday close hour (server time)
input int    maxTradesDay   = 2;      // Max trades per day
input int    magicNumber    = 20260914;
input int    slippagePoints = 20;

input group "Trend Duration Forecast"
input int    tdLength       = 50;     // HMA Smoothing Length
input int    tdTrendLength  = 3;      // Trend Detection Sensitivity (bars)
input int    tdSamples      = 10;     // Trend Sample Size

input group "RSI Divergence"
input int    rsiLength              = 14;
input bool   useRsiDivergenceFilter = false;

input group "Integration"
input bool   useHmaFilter = false;
input bool   enableAlerts = true;
input bool   enablePush   = false;

input group "Debug"
input bool   debugLogging = true;

//======================================================================
// GLOBALS
//======================================================================
int ema20Handle    = INVALID_HANDLE;
int ema50h4Handle  = INVALID_HANDLE;
int ema200h4Handle = INVALID_HANDLE;
int atrHandle      = INVALID_HANDLE;
int rsiHandle      = INVALID_HANDLE;

datetime lastBarTime = 0;

enum TrendState { TREND_NONE = 0, TREND_UP = 1, TREND_DOWN = -1 };
TrendState tdTrend     = TREND_NONE;
TrendState tdPrevTrend = TREND_NONE;
int tdTrendCount       = 0;

double bullDurations[];
double bearDurations[];

int tradesToday   = 0;
int lastTradedDay = -1;   // day-of-year

// Swing structure (levels + RSI at swing)
double lastSwingHigh = 0, prevSwingHigh = 0;
double lastSwingHighRSI = 0, prevSwingHighRSI = 0;
datetime lastSwingHighTime = 0;
bool haveSwingHigh = false, havePrevSwingHigh = false;

double lastSwingLow = 0, prevSwingLow = 0;
double lastSwingLowRSI = 0, prevSwingLowRSI = 0;
datetime lastSwingLowTime = 0;
bool haveSwingLow = false, havePrevSwingLow = false;

//======================================================================
// INIT
//======================================================================
int OnInit()
  {
   ema20Handle    = iMA(_Symbol, PERIOD_CURRENT, 20, 0, MODE_EMA, PRICE_CLOSE);
   ema50h4Handle  = iMA(_Symbol, PERIOD_H4, 50, 0, MODE_EMA, PRICE_CLOSE);
   ema200h4Handle = iMA(_Symbol, PERIOD_H4, 200, 0, MODE_EMA, PRICE_CLOSE);
   atrHandle      = iATR(_Symbol, PERIOD_CURRENT, 14);
   rsiHandle      = iRSI(_Symbol, PERIOD_CURRENT, rsiLength, PRICE_CLOSE);

   if(ema20Handle == INVALID_HANDLE || ema50h4Handle == INVALID_HANDLE ||
      ema200h4Handle == INVALID_HANDLE || atrHandle == INVALID_HANDLE ||
      rsiHandle == INVALID_HANDLE)
     {
      Print("OnInit FAILED: could not create one or more indicator handles");
      return(INIT_FAILED);
     }

   trade.SetExpertMagicNumber(magicNumber);
   trade.SetDeviationInPoints(slippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetAsyncMode(false);

   ArrayResize(bullDurations, 0);
   ArrayResize(bearDurations, 0);

   // Sanity check: does the chart have usable history right now?
   int barsOnChart = Bars(_Symbol, PERIOD_CURRENT);
   int barsH4      = Bars(_Symbol, PERIOD_H4);
   PrintFormat("OnInit OK | %s %s | chart bars=%d | H4 bars=%d | first chart bar=%s",
               _Symbol, EnumToString((ENUM_TIMEFRAMES)Period()),
               barsOnChart, barsH4,
               TimeToString(iTime(_Symbol, PERIOD_CURRENT, barsOnChart - 1)));

   if(barsH4 < 200)
      Print("WARNING: fewer than 200 H4 bars available. H4 EMA200 will not be ready until history is downloaded.");

   return(INIT_SUCCEEDED);
  }

//======================================================================
void OnDeinit(const int reason)
  {
   if(ema20Handle    != INVALID_HANDLE) IndicatorRelease(ema20Handle);
   if(ema50h4Handle  != INVALID_HANDLE) IndicatorRelease(ema50h4Handle);
   if(ema200h4Handle != INVALID_HANDLE) IndicatorRelease(ema200h4Handle);
   if(atrHandle      != INVALID_HANDLE) IndicatorRelease(atrHandle);
   if(rsiHandle      != INVALID_HANDLE) IndicatorRelease(rsiHandle);
   Comment("");
  }

//======================================================================
// MATH HELPERS
//======================================================================
// WMA over series array (index 0 = most recent, weights decrease as we go
// back in time within [start, start+period-1]).
double WMA(const double &price[], int start, int period)
  {
   double sum = 0.0, wsum = 0.0;
   for(int i = 0; i < period; i++)
     {
      double w = (double)(period - i);
      sum  += price[start + i] * w;
      wsum += w;
     }
   return(wsum > 0.0 ? sum / wsum : price[start]);
  }

// Hull MA at offset 'start'. price[] is series-ordered.
double HMA_At(const double &price[], int start, int period)
  {
   int halfPeriod = MathMax(1, period / 2);
   int sqrtPeriod = MathMax(1, (int)MathRound(MathSqrt((double)period)));

   double diff[];
   ArrayResize(diff, sqrtPeriod);
   for(int j = 0; j < sqrtPeriod; j++)
     {
      double wmaHalf = WMA(price, start + j, halfPeriod);
      double wmaFull = WMA(price, start + j, period);
      diff[j] = 2.0 * wmaHalf - wmaFull;
     }
   return WMA(diff, 0, sqrtPeriod);
  }

//======================================================================
// TREND DURATION
//======================================================================
void PushDuration(double &arr[], int value)
  {
   int n = ArraySize(arr);
   ArrayResize(arr, n + 1);
   arr[n] = (double)value;
   n++;
   if(n > tdSamples)
     {
      for(int i = 0; i < n - 1; i++) arr[i] = arr[i + 1];
      ArrayResize(arr, n - 1);
     }
  }

double AvgOrZero(const double &arr[])
  {
   int n = ArraySize(arr);
   if(n == 0) return(0.0);
   double s = 0.0;
   for(int i = 0; i < n; i++) s += arr[i];
   return(s / (double)n);
  }

string FmtAvg(double v, int n) { return(n == 0 ? "n/a" : DoubleToString(v, 0)); }

// Uses last closed bars to classify the HMA trend and count duration.
void UpdateTrendDuration()
  {
   int neededCloses = tdLength + (int)MathRound(MathSqrt((double)tdLength)) + tdTrendLength + 5;
   double closes[];
   ArraySetAsSeries(closes, true);
   int copied = CopyClose(_Symbol, PERIOD_CURRENT, 1, neededCloses, closes);
   if(copied < neededCloses) return;

   int hmaPoints = tdTrendLength + 1;
   double hma[];
   ArrayResize(hma, hmaPoints);
   for(int k = 0; k < hmaPoints; k++)
      hma[k] = HMA_At(closes, k, tdLength);

   bool rising = true, falling = true;
   for(int k = 0; k < tdTrendLength; k++)
     {
      if(!(hma[k] > hma[k + 1])) rising  = false;
      if(!(hma[k] < hma[k + 1])) falling = false;
     }

   TrendState newTrend = TREND_NONE;
   if(rising)        newTrend = TREND_UP;
   else if(falling)  newTrend = TREND_DOWN;

   if(newTrend == TREND_NONE)
     {
      // Trend cancelled (HMA flat) — close out the current run.
      if(tdTrend == TREND_UP)   PushDuration(bullDurations, tdTrendCount);
      if(tdTrend == TREND_DOWN) PushDuration(bearDurations, tdTrendCount);
      tdPrevTrend = tdTrend;
      tdTrend     = TREND_NONE;
      tdTrendCount = 0;
      return;
     }

   if(newTrend != tdTrend)
     {
      // Previous run just ended; record its length.
      if(tdTrend == TREND_UP)   PushDuration(bullDurations, tdTrendCount);
      if(tdTrend == TREND_DOWN) PushDuration(bearDurations, tdTrendCount);
      tdPrevTrend  = tdTrend;
      tdTrend      = newTrend;
      tdTrendCount = 1;
     }
   else
     {
      tdTrendCount++;
     }
  }

//======================================================================
// PIVOTS
//======================================================================
// Confirmed pivot high: bar `swingLen` back is strictly the highest among
// [0 .. 2*swingLen] bars, using bars shifted by 1 (excludes forming bar).
// Returns true only ONCE per pivot (deduped via lastSwingHighTime).
bool GetConfirmedPivotHigh(double &level, double &rsiAtPivot, datetime &pivotTime)
  {
   int need = 2 * swingLen + 1;
   double highs[], rsiBuf[];
   ArraySetAsSeries(highs,  true);
   ArraySetAsSeries(rsiBuf, true);

   if(CopyHigh(_Symbol, PERIOD_CURRENT, 1, need, highs) < need) return(false);
   if(CopyBuffer(rsiHandle, 0, 1, need, rsiBuf)   < need) return(false);

   int    cand  = swingLen;
   double val   = highs[cand];

   for(int i = 0; i < cand; i++)
      if(highs[i] >= val) return(false);
   for(int i = cand + 1; i < need; i++)
      if(highs[i] >= val) return(false);

   datetime t = iTime(_Symbol, PERIOD_CURRENT, 1 + cand);
   if(t == 0) return(false);
   if(t == lastSwingHighTime) return(false);   // already registered this pivot

   level      = val;
   rsiAtPivot = rsiBuf[cand];
   pivotTime  = t;
   return(true);
  }

bool GetConfirmedPivotLow(double &level, double &rsiAtPivot, datetime &pivotTime)
  {
   int need = 2 * swingLen + 1;
   double lows[], rsiBuf[];
   ArraySetAsSeries(lows,   true);
   ArraySetAsSeries(rsiBuf, true);

   if(CopyLow(_Symbol, PERIOD_CURRENT, 1, need, lows) < need) return(false);
   if(CopyBuffer(rsiHandle, 0, 1, need, rsiBuf)       < need) return(false);

   int    cand = swingLen;
   double val  = lows[cand];

   for(int i = 0; i < cand; i++)
      if(lows[i] <= val) return(false);
   for(int i = cand + 1; i < need; i++)
      if(lows[i] <= val) return(false);

   datetime t = iTime(_Symbol, PERIOD_CURRENT, 1 + cand);
   if(t == 0) return(false);
   if(t == lastSwingLowTime) return(false);

   level      = val;
   rsiAtPivot = rsiBuf[cand];
   pivotTime  = t;
   return(true);
  }

//======================================================================
// POSITION HELPERS
//======================================================================
bool HasOpenPosition()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) == magicNumber &&
         PositionGetString(POSITION_SYMBOL) == _Symbol)
         return(true);
     }
   return(false);
  }

void CloseAllPositions(string reason)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) == magicNumber &&
         PositionGetString(POSITION_SYMBOL) == _Symbol)
        {
         if(trade.PositionClose(ticket))
            PrintFormat("Closed #%I64u (%s)", ticket, reason);
        }
     }
  }

//======================================================================
// SIZING
//======================================================================
double NormalizeLots(double lots)
  {
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double stepLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(stepLot <= 0.0) stepLot = 0.01;
   lots = MathFloor(lots / stepLot) * stepLot;
   lots = MathMax(minLot, MathMin(maxLot, lots));
   int digits = 2;
   if(stepLot < 0.01) digits = 3;
   return(NormalizeDouble(lots, digits));
  }

double CalcLotsForRisk(double entry, double sl)
  {
   double balance    = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskAmount = balance * riskPercent / 100.0;
   double stopDist   = MathAbs(entry - sl);
   double tickValue  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(stopDist <= 0 || tickSize <= 0 || tickValue <= 0) return(0.0);

   double moneyPerLot = (stopDist / tickSize) * tickValue;
   if(moneyPerLot <= 0) return(0.0);

   return NormalizeLots(riskAmount / moneyPerLot);
  }

//======================================================================
// ALERTS
//======================================================================
void FireSignalAlert(bool isLong, double entry, double sl, double tp,
                     double rsiNow, bool divergencePresent)
  {
   string dir = isLong ? "LONG" : "SHORT";

   int    n         = isLong ? ArraySize(bullDurations) : ArraySize(bearDurations);
   double avgDur    = isLong ? AvgOrZero(bullDurations) : AvgOrZero(bearDurations);
   string avgStr    = FmtAvg(avgDur, n);
   string remainStr = (n > 0) ? DoubleToString(avgDur - tdTrendCount, 0) : "n/a";

   string trendLabel = (tdTrend == TREND_UP)   ? "UP"
                     : (tdTrend == TREND_DOWN) ? "DOWN" : "NONE";
   string divLabel   = divergencePresent ? "confirmed" : "not present";

   string msg = StringFormat(
      "%s signal | %s\nEntry %.5f  SL %.5f  TP %.5f\nHMA trend: %s, %d bars in (avg %s, ~%s bars left typically)\nRSI(%d): %.1f | divergence: %s",
      dir, _Symbol, entry, sl, tp, trendLabel, tdTrendCount,
      avgStr, remainStr, rsiLength, rsiNow, divLabel);

   Comment(msg);
   if(enableAlerts) Alert(msg);
   if(enablePush)   SendNotification(msg);
   Print(msg);
  }

//======================================================================
// MAIN
//======================================================================
void OnTick()
  {
   datetime curBarTime = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(curBarTime == 0) return;
   if(curBarTime == lastBarTime) return;    // wait for a new closed bar
   lastBarTime = curBarTime;

   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);

   // ---- daily trade counter (use day-of-year for reliability) ----
   if(dt.day_of_year != lastTradedDay)
     {
      tradesToday   = 0;
      lastTradedDay = dt.day_of_year;
     }

   // ---- Friday force-close ----
   bool isFriday        = (dt.day_of_week == 5);
   bool fridayCloseTime = isFriday && (dt.hour >= fridayHour);
   if(closeByFriday && fridayCloseTime)
      CloseAllPositions("Friday close");

   // ---- update trend duration ----
   UpdateTrendDuration();

   // ---- update swing structure + RSI-at-swing ----
   double   lvl = 0, rsiAtPivot = 0;
   datetime pTime = 0;

   if(GetConfirmedPivotHigh(lvl, rsiAtPivot, pTime))
     {
      prevSwingHigh     = lastSwingHigh;
      prevSwingHighRSI  = lastSwingHighRSI;
      havePrevSwingHigh = haveSwingHigh;

      lastSwingHigh     = lvl;
      lastSwingHighRSI  = rsiAtPivot;
      lastSwingHighTime = pTime;
      haveSwingHigh     = true;

      if(debugLogging)
         PrintFormat("New swing HIGH %.5f (RSI %.1f) @ %s", lvl, rsiAtPivot, TimeToString(pTime));
     }

   if(GetConfirmedPivotLow(lvl, rsiAtPivot, pTime))
     {
      prevSwingLow     = lastSwingLow;
      prevSwingLowRSI  = lastSwingLowRSI;
      havePrevSwingLow = haveSwingLow;

      lastSwingLow     = lvl;
      lastSwingLowRSI  = rsiAtPivot;
      lastSwingLowTime = pTime;
      haveSwingLow     = true;

      if(debugLogging)
         PrintFormat("New swing LOW %.5f (RSI %.1f) @ %s", lvl, rsiAtPivot, TimeToString(pTime));
     }

   // ---- divergence from the two most recent confirmed swings ----
   bool bullishDivergence = haveSwingLow && havePrevSwingLow &&
                            lastSwingLow < prevSwingLow &&
                            lastSwingLowRSI > prevSwingLowRSI;

   bool bearishDivergence = haveSwingHigh && havePrevSwingHigh &&
                            lastSwingHigh > prevSwingHigh &&
                            lastSwingHighRSI < prevSwingHighRSI;

   // ---- indicator buffers (use last CLOSED bar index 1) ----
   double ema20buf[1], ema50buf[1], ema200buf[1], atrBuf[1], rsiBuf[1];

   if(CopyBuffer(ema20Handle, 0, 1, 1, ema20buf) < 1)
     { if(debugLogging) PrintFormat("Bar %s: EMA20 not ready", TimeToString(curBarTime)); return; }
   if(CopyBuffer(ema50h4Handle, 0, 0, 1, ema50buf) < 1)
     { if(debugLogging) PrintFormat("Bar %s: EMA50(H4) not ready", TimeToString(curBarTime)); return; }
   if(CopyBuffer(ema200h4Handle, 0, 0, 1, ema200buf) < 1)
     { if(debugLogging) PrintFormat("Bar %s: EMA200(H4) not ready", TimeToString(curBarTime)); return; }
   if(CopyBuffer(atrHandle, 0, 1, 1, atrBuf) < 1)
     { if(debugLogging) PrintFormat("Bar %s: ATR not ready", TimeToString(curBarTime)); return; }
   if(CopyBuffer(rsiHandle, 0, 1, 1, rsiBuf) < 1)
     { if(debugLogging) PrintFormat("Bar %s: RSI not ready", TimeToString(curBarTime)); return; }

   double ema20    = ema20buf[0];
   double ema50h4  = ema50buf[0];
   double ema200h4 = ema200buf[0];
   double rsiNow   = rsiBuf[0];

   bool uptrend   = ema50h4 > ema200h4;
   bool downtrend = ema50h4 < ema200h4;

   // ---- OHLC of last two closed bars ----
   double o[2], c[2], h[2], l[2];
   ArraySetAsSeries(o, true); ArraySetAsSeries(c, true);
   ArraySetAsSeries(h, true); ArraySetAsSeries(l, true);

   if(CopyOpen (_Symbol, PERIOD_CURRENT, 1, 2, o) < 2) return;
   if(CopyClose(_Symbol, PERIOD_CURRENT, 1, 2, c) < 2) return;
   if(CopyHigh (_Symbol, PERIOD_CURRENT, 1, 2, h) < 2) return;
   if(CopyLow  (_Symbol, PERIOD_CURRENT, 1, 2, l) < 2) return;

   double open1 = o[0], close1 = c[0], high1 = h[0], low1 = l[0];
   double open2 = o[1], close2 = c[1];

   // ---- pattern checks ----
   bool touchedEMA    = (low1 <= ema20 && high1 >= ema20);
   bool bullishEngulf = (close1 > open1) && (close2 < open2) &&
                        (close1 > open2) && (open1 < close2);
   bool bearishEngulf = (close1 < open1) && (close2 > open2) &&
                        (close1 < open2) && (open1 > close2);

   bool rawLong  = uptrend   && touchedEMA && bullishEngulf;
   bool rawShort = downtrend && touchedEMA && bearishEngulf;

   // ---- optional filters ----
   bool hmaOkLong  = !useHmaFilter || (tdTrend == TREND_UP);
   bool hmaOkShort = !useHmaFilter || (tdTrend == TREND_DOWN);
   rawLong  = rawLong  && hmaOkLong;
   rawShort = rawShort && hmaOkShort;

   bool divOkLong  = !useRsiDivergenceFilter || bullishDivergence;
   bool divOkShort = !useRsiDivergenceFilter || bearishDivergence;
   rawLong  = rawLong  && divOkLong;
   rawShort = rawShort && divOkShort;

   if(debugLogging)
      PrintFormat("Bar %s | up=%d dn=%d touch=%d bull=%d bear=%d swingLo=%d swingHi=%d rawL=%d rawS=%d",
                  TimeToString(curBarTime),
                  uptrend, downtrend, touchedEMA,
                  bullishEngulf, bearishEngulf,
                  haveSwingLow, haveSwingHigh,
                  rawLong, rawShort);

   // ---- build signal levels (using closed-bar close as reference) ----
   double entryPrice = 0, sl = 0, tp = 0;
   bool longSignal = false, shortSignal = false;
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   if(rawLong && haveSwingLow)
     {
      entryPrice = close1;
      sl         = lastSwingLow;
      double stopDist = entryPrice - sl;
      if(stopDist > 0)
        {
         tp = entryPrice + stopDist * rrRatio;

         if(useRoomFilter && haveSwingHigh &&
            lastSwingHigh > entryPrice && lastSwingHigh < tp)
           {
            double fullDist = tp - entryPrice;
            double roomDist = lastSwingHigh - entryPrice;
            if(fullDist > 0 && (roomDist / fullDist) < minRoomRatio)
               tp = lastSwingHigh - point * 50;
           }
         if((tp - entryPrice) >= stopDist) longSignal = true;
        }
     }

   if(rawShort && haveSwingHigh)
     {
      entryPrice = close1;
      sl         = lastSwingHigh;
      double stopDist = sl - entryPrice;
      if(stopDist > 0)
        {
         tp = entryPrice - stopDist * rrRatio;

         if(useRoomFilter && haveSwingLow &&
            lastSwingLow < entryPrice && lastSwingLow > tp)
           {
            double fullDist = entryPrice - tp;
            double roomDist = entryPrice - lastSwingLow;
            if(fullDist > 0 && (roomDist / fullDist) < minRoomRatio)
               tp = lastSwingLow + point * 50;
           }
         if((entryPrice - tp) >= stopDist) shortSignal = true;
        }
     }

   // ---- fire alerts on signal ----
   if(longSignal)
      FireSignalAlert(true,  entryPrice, sl, tp, rsiNow, bullishDivergence);
   else if(shortSignal)
      FireSignalAlert(false, entryPrice, sl, tp, rsiNow, bearishDivergence);

   // ---- execution gating ----
   bool canTrade = !fridayCloseTime &&
                   tradesToday < maxTradesDay &&
                   !HasOpenPosition();
   if(!canTrade) return;

   if(longSignal)
     {
      double ask      = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double slLevel  = lastSwingLow;
      double stopDist = ask - slLevel;
      if(stopDist <= 0) return;

      double tpLevel  = ask + stopDist * rrRatio;

      if(useRoomFilter && haveSwingHigh &&
         lastSwingHigh > ask && lastSwingHigh < tpLevel)
        {
         double fullDist = tpLevel - ask;
         double roomDist = lastSwingHigh - ask;
         if(fullDist > 0 && (roomDist / fullDist) < minRoomRatio)
            tpLevel = lastSwingHigh - point * 50;
        }

      double lots = CalcLotsForRisk(ask, slLevel);
      if(lots > 0 && trade.Buy(lots, _Symbol, ask, slLevel, tpLevel, "EMA Pullback Long"))
        {
         tradesToday++;
         PrintFormat("BUY %.2f lots @ %.5f SL %.5f TP %.5f", lots, ask, slLevel, tpLevel);
        }
      else if(lots > 0)
         PrintFormat("Buy failed: ret=%d desc=%s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
     }
   else if(shortSignal)
     {
      double bid      = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double slLevel  = lastSwingHigh;
      double stopDist = slLevel - bid;
      if(stopDist <= 0) return;

      double tpLevel  = bid - stopDist * rrRatio;

      if(useRoomFilter && haveSwingLow &&
         lastSwingLow < bid && lastSwingLow > tpLevel)
        {
         double fullDist = bid - tpLevel;
         double roomDist = bid - lastSwingLow;
         if(fullDist > 0 && (roomDist / fullDist) < minRoomRatio)
            tpLevel = lastSwingLow + point * 50;
        }

      double lots = CalcLotsForRisk(bid, slLevel);
      if(lots > 0 && trade.Sell(lots, _Symbol, bid, slLevel, tpLevel, "EMA Pullback Short"))
        {
         tradesToday++;
         PrintFormat("SELL %.2f lots @ %.5f SL %.5f TP %.5f", lots, bid, slLevel, tpLevel);
        }
      else if(lots > 0)
         PrintFormat("Sell failed: ret=%d desc=%s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
     }
  }


// ====================================================================
// SAJIM BENCHMARK HARVESTER INJECTION (AUTOMATED)
// ====================================================================
#include <SajimLogger.mqh>

double OnTester()
{
    SajimHarvestTrades("EMA_Pullback_TrendDuration_EA", _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
    return 0.0;
}
