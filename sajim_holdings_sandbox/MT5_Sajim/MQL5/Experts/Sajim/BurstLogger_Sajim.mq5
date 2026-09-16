//+------------------------------------------------------------------+
//| BurstLogger.mq5                                                  |
//| Detects bursts, logs forward price action. No trading.           |
//+------------------------------------------------------------------+
#property copyright "YourName"
#property version   "1.00"
#property strict

//--- burst detection (same logic as the scalper)
input int      BurstWindowMs     = 500;
input int      BaselineWindowMs  = 5000;
input double   BurstMult         = 1.5;
input int      MinTicksInBurst   = 8;
input double   DirectionRatio    = 1.15;

//--- forward horizons to measure
input int      Horizon1Ms        = 500;
input int      Horizon2Ms        = 2000;
input int      Horizon3Ms        = 5000;
input int      Horizon4Ms        = 10000;

//--- optional
input bool     SkipWideSpread    = false;
input int      MaxSpreadPoints   = 30;
input bool     PrintSummary      = true;

//--- tick buffer
struct TickRec { ulong t_ms; double price; int dir; };
TickRec g_ticks[];

//--- burst records
struct BurstRec
{
   ulong  burstMs;
   int    dir;
   double rate;
   int    tickCount;
   int    ups;
   int    downs;
   double priceBurst;
   double price1; bool done1;
   double price2; bool done2;
   double price3; bool done3;
   double price4; bool done4;
};
BurstRec g_bursts[];
int      g_firstPending = 0;

int    g_lastDir = 0;
double g_lastPrice = 0.0;
ulong  g_currentMs = 0;

string g_filename = "";

//+------------------------------------------------------------------+
int OnInit()
{
   ArrayResize(g_ticks, 0);
   ArrayResize(g_bursts, 0);
   g_lastPrice = 0.0;

   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   g_filename = StringFormat("BurstLog_%s_%04d%02d%02d_%02d%02d%02d.csv",
                             _Symbol,
                             dt.year, dt.mon, dt.day,
                             dt.hour, dt.min, dt.sec);

   PrintFormat("[INIT] %s point=%.5f digits=%d spread=%d",
               _Symbol,
               SymbolInfoDouble(_Symbol, SYMBOL_POINT),
               _Digits,
               (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD));
   PrintFormat("[INIT] Burst: window=%dms baseline=%dms mult=%.2f minTicks=%d dirRatio=%.2f",
               BurstWindowMs, BaselineWindowMs, BurstMult, MinTicksInBurst, DirectionRatio);
   PrintFormat("[INIT] Horizons: %d / %d / %d / %d ms",
               Horizon1Ms, Horizon2Ms, Horizon3Ms, Horizon4Ms);
   PrintFormat("[INIT] Writing to %s", g_filename);

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   FlushToCsv();
   if(PrintSummary) PrintStats();
}

//+------------------------------------------------------------------+
void PurgeOldTicks(ulong cutoff)
{
   int n = ArraySize(g_ticks), firstKeep = 0;
   while(firstKeep < n && g_ticks[firstKeep].t_ms < cutoff) firstKeep++;
   if(firstKeep == 0) return;
   if(firstKeep >= n) { ArrayResize(g_ticks, 0); return; }
   int newSize = n - firstKeep;
   for(int i = 0; i < newSize; i++) g_ticks[i] = g_ticks[firstKeep + i];
   ArrayResize(g_ticks, newSize);
}

//+------------------------------------------------------------------+
void RecordTick(ulong tms)
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double mid = (bid + ask) * 0.5;

   int dir = 0;
   if(g_lastPrice > 0.0)
   {
      if(mid > g_lastPrice) dir = 1;
      else if(mid < g_lastPrice) dir = -1;
      else dir = g_lastDir;
   }
   g_lastPrice = mid;
   g_lastDir = dir;

   int n = ArraySize(g_ticks);
   ArrayResize(g_ticks, n + 1);
   g_ticks[n].t_ms  = tms;
   g_ticks[n].price = mid;
   g_ticks[n].dir   = dir;
}

//+------------------------------------------------------------------+
double BurstRate(int &dirOut, int &burstCountOut, int &upsOut, int &downsOut)
{
   dirOut = 0; burstCountOut = 0; upsOut = 0; downsOut = 0;

   ulong now = g_currentMs;
   ulong burstCut = now - (ulong)BurstWindowMs;
   int total = ArraySize(g_ticks);
   if(total < 1) return 0.0;

   int burstCount = 0, ups = 0, downs = 0;
   for(int i = total - 1; i >= 0; i--)
   {
      if(g_ticks[i].t_ms < burstCut) break;
      burstCount++;
      if(g_ticks[i].dir > 0) ups++;
      else if(g_ticks[i].dir < 0) downs++;
   }
   burstCountOut = burstCount;
   upsOut = ups; downsOut = downs;

   ulong baseCut = now - (ulong)BaselineWindowMs;
   int baselineCount = 0;
   for(int i = total - 1; i >= 0; i--)
   {
      if(g_ticks[i].t_ms < baseCut) break;
      baselineCount++;
   }
   baselineCount -= burstCount;
   if(baselineCount < 1) baselineCount = 1;

   double baseSpanMs = (double)(BaselineWindowMs - BurstWindowMs);
   if(baseSpanMs <= 0) baseSpanMs = 1.0;
   double baselineRatePerMs = (double)baselineCount / baseSpanMs;
   double expectedInBurst = baselineRatePerMs * (double)BurstWindowMs;
   if(expectedInBurst < 1.0) expectedInBurst = 1.0;

   double ratio = (double)burstCount / expectedInBurst;

   if(ups   > 0 && (double)ups   >= (double)downs * DirectionRatio) dirOut =  1;
   else if(downs > 0 && (double)downs >= (double)ups * DirectionRatio) dirOut = -1;

   return ratio;
}

//+------------------------------------------------------------------+
void AddBurst(int dir, double rate, int ticks, int ups, int downs, double price)
{
   int n = ArraySize(g_bursts);
   ArrayResize(g_bursts, n + 1);
   g_bursts[n].burstMs    = g_currentMs;
   g_bursts[n].dir        = dir;
   g_bursts[n].rate       = rate;
   g_bursts[n].tickCount  = ticks;
   g_bursts[n].ups        = ups;
   g_bursts[n].downs      = downs;
   g_bursts[n].priceBurst = price;
   g_bursts[n].price1 = 0; g_bursts[n].done1 = false;
   g_bursts[n].price2 = 0; g_bursts[n].done2 = false;
   g_bursts[n].price3 = 0; g_bursts[n].done3 = false;
   g_bursts[n].price4 = 0; g_bursts[n].done4 = false;
}

//+------------------------------------------------------------------+
void ResolvePending(double curMid)
{
   ulong now = g_currentMs;
   int n = ArraySize(g_bursts);

   for(int i = g_firstPending; i < n; i++)
   {
      ulong elapsed = now - g_bursts[i].burstMs;

      // oldest unresolved hasn't reached shortest horizon → none have
      if(i == g_firstPending && elapsed < (ulong)Horizon1Ms)
         break;

      if(!g_bursts[i].done1 && elapsed >= (ulong)Horizon1Ms) { g_bursts[i].price1 = curMid; g_bursts[i].done1 = true; }
      if(!g_bursts[i].done2 && elapsed >= (ulong)Horizon2Ms) { g_bursts[i].price2 = curMid; g_bursts[i].done2 = true; }
      if(!g_bursts[i].done3 && elapsed >= (ulong)Horizon3Ms) { g_bursts[i].price3 = curMid; g_bursts[i].done3 = true; }
      if(!g_bursts[i].done4 && elapsed >= (ulong)Horizon4Ms) { g_bursts[i].price4 = curMid; g_bursts[i].done4 = true; }

      if(g_bursts[i].done1 && g_bursts[i].done2 && g_bursts[i].done3 && g_bursts[i].done4
         && i == g_firstPending)
         g_firstPending = i + 1;
   }
}

//+------------------------------------------------------------------+
void FlushToCsv()
{
   int h = FileOpen(g_filename, FILE_WRITE|FILE_CSV|FILE_ANSI);
   if(h == INVALID_HANDLE)
   {
      PrintFormat("[ERROR] Cannot open %s for writing, err=%d",
                  g_filename, GetLastError());
      return;
   }

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   FileWrite(h,
      "idx","time_str","time_msc","dir","rate","ticks","ups","downs",
      "price_burst",
      "price_500ms","move_500ms_pts",
      "price_2s","move_2s_pts",
      "price_5s","move_5s_pts",
      "price_10s","move_10s_pts");

   int n = ArraySize(g_bursts);
   for(int i = 0; i < n; i++)
   {
      double p  = g_bursts[i].priceBurst;
      int    d  = g_bursts[i].dir;

      string p1s="", m1="", p2s="", m2="", p3s="", m3="", p4s="", m4="";

      if(g_bursts[i].done1 && point > 0.0)
      {
         p1s = DoubleToString(g_bursts[i].price1, _Digits);
         m1  = DoubleToString((g_bursts[i].price1 - p) / point * d, 1);
      }
      if(g_bursts[i].done2 && point > 0.0)
      {
         p2s = DoubleToString(g_bursts[i].price2, _Digits);
         m2  = DoubleToString((g_bursts[i].price2 - p) / point * d, 1);
      }
      if(g_bursts[i].done3 && point > 0.0)
      {
         p3s = DoubleToString(g_bursts[i].price3, _Digits);
         m3  = DoubleToString((g_bursts[i].price3 - p) / point * d, 1);
      }
      if(g_bursts[i].done4 && point > 0.0)
      {
         p4s = DoubleToString(g_bursts[i].price4, _Digits);
         m4  = DoubleToString((g_bursts[i].price4 - p) / point * d, 1);
      }

      FileWrite(h,
         i,
         TimeToString((datetime)(g_bursts[i].burstMs / 1000), TIME_DATE|TIME_SECONDS),
         (long)g_bursts[i].burstMs,
         d,
         DoubleToString(g_bursts[i].rate, 3),
         g_bursts[i].tickCount,
         g_bursts[i].ups,
         g_bursts[i].downs,
         DoubleToString(p, _Digits),
         p1s, m1,
         p2s, m2,
         p3s, m3,
         p4s, m4);
   }

   FileClose(h);
   PrintFormat("[CSV] Wrote %d bursts to %s", n, g_filename);
}

//+------------------------------------------------------------------+
void PrintStats()
{
   int n = ArraySize(g_bursts);
   if(n == 0) { Print("[STATS] No bursts detected"); return; }

   int wins1=0, wins2=0, wins3=0, wins4=0;
   int tot1=0, tot2=0, tot3=0, tot4=0;
   double sum1=0, sum2=0, sum3=0, sum4=0;

   for(int i = 0; i < n; i++)
   {
      double p = g_bursts[i].priceBurst;
      int    d = g_bursts[i].dir;

      if(g_bursts[i].done1) { double m=(g_bursts[i].price1-p)*d; tot1++; if(m>0)wins1++; sum1+=m; }
      if(g_bursts[i].done2) { double m=(g_bursts[i].price2-p)*d; tot2++; if(m>0)wins2++; sum2+=m; }
      if(g_bursts[i].done3) { double m=(g_bursts[i].price3-p)*d; tot3++; if(m>0)wins3++; sum3+=m; }
      if(g_bursts[i].done4) { double m=(g_bursts[i].price4-p)*d; tot4++; if(m>0)wins4++; sum4+=m; }
   }

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double toPts = (point > 0.0) ? (1.0 / point) : 1.0;

   Print("------------------------------------------------------------");
   PrintFormat("[STATS] Total bursts: %d", n);
   PrintFormat("[STATS] %5dms: n=%d  winRate=%.1f%%  avgMove=%.2f pts",
               Horizon1Ms, tot1, tot1?100.0*wins1/tot1:0.0, tot1?sum1/tot1*toPts:0.0);
   PrintFormat("[STATS] %5dms: n=%d  winRate=%.1f%%  avgMove=%.2f pts",
               Horizon2Ms, tot2, tot2?100.0*wins2/tot2:0.0, tot2?sum2/tot2*toPts:0.0);
   PrintFormat("[STATS] %5dms: n=%d  winRate=%.1f%%  avgMove=%.2f pts",
               Horizon3Ms, tot3, tot3?100.0*wins3/tot3:0.0, tot3?sum3/tot3*toPts:0.0);
   PrintFormat("[STATS] %5dms: n=%d  winRate=%.1f%%  avgMove=%.2f pts",
               Horizon4Ms, tot4, tot4?100.0*wins4/tot4:0.0, tot4?sum4/tot4*toPts:0.0);
   Print("[STATS] Interpretation:");
   Print("[STATS]   winRate > 55%%  → signal has predictive edge, follow it");
   Print("[STATS]   45–55%%        → noise, need different trigger");
   Print("[STATS]   < 45%%         → signal is anti-predictive, fade it");
   Print("------------------------------------------------------------");
}

//+------------------------------------------------------------------+
void OnTick()
{
   MqlTick t;
   if(!SymbolInfoTick(_Symbol, t)) return;
   g_currentMs = t.time_msc;

   RecordTick(g_currentMs);
   ulong cutoff = g_currentMs - (ulong)(BaselineWindowMs + 1000);
   PurgeOldTicks(cutoff);

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double mid = (bid + ask) * 0.5;

   ResolvePending(mid);

   if(SkipWideSpread)
   {
      long sp = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      if(sp > MaxSpreadPoints) return;
   }

   int dir = 0, bc = 0, ups = 0, downs = 0;
   double rate = BurstRate(dir, bc, ups, downs);




   if(rate >= BurstMult && bc >= MinTicksInBurst && dir != 0)
   {
      AddBurst(dir, rate, bc, ups, downs, mid);

      PrintFormat("[BURST] t=%s dir=%d rate=%.2f ticks=%d up=%d dn=%d price=%.*f",
                  TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS),
                  dir, rate, bc, ups, downs, _Digits, mid);
   }
}
//+------------------------------------------------------------------+


// ====================================================================
// SAJIM BENCHMARK HARVESTER INJECTION (AUTOMATED)
// ====================================================================
#include <SajimLogger.mqh>

double OnTester()
{
    SajimHarvestTrades("BurstLogger", _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
    return 0.0;
}
