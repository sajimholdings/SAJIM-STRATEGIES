//+------------------------------------------------------------------+
//|                                     MCP_VolumePulse_Sajim.mq5     |
//|                               SAJIM Quant - Micro-Volume Scalper  |
//|                                        https://sajimholdings.com |
//+------------------------------------------------------------------+
#property copyright "SAJIM Quant Lab"
#property link      "https://sajimholdings.com"
#property version   "1.00"
#property description "High-Frequency Micro-Volume Pulse Scalper for XAUUSD"
#property description "Captures M1 tick velocity spikes that build M5 expansion candles."
#property strict

#include <Trade\Trade.mqh>

//+------------------------------------------------------------------+
//| Input Parameters                                                  |
//+------------------------------------------------------------------+
input group "=== Pyramiding Position Sizing ==="
input double InpLot_Tier1             = 0.10;       // Tier 1 Lot Size (Ignition Entry)
input double InpLot_Tier2             = 0.07;       // Tier 2 Lot Size (Continuation Add-on 1)
input double InpLot_Tier3             = 0.03;       // Tier 3 Lot Size (Continuation Add-on 2)
input int    InpMinAddonPoints        = 20;         // Minimum price advance between add-ons (pts)
input int    InpMinAddonIntervalSec   = 3;          // Minimum seconds between add-on executions

input group "=== Volume & Velocity Engine ==="
input int    InpVelocityWindow_ms     = 3000;       // Rolling tick window (milliseconds)
input int    InpVelocityTrigger       = 20;         // Ignition threshold (ticks within window)
input int    InpVelocityContinuation  = 14;         // Continuation threshold for add-ons (ticks)
input double InpDirectionThreshold    = 0.70;       // Aggression ratio (70% upticks or downticks)

input group "=== Dynamic Volume-Drop & Exit ==="
input int    InpMinHoldSeconds           = 15;         // Grace period: min seconds before velocity exhaustion exit
input double InpDecelExitRatio           = 0.35;       // Exit when velocity drops below this % of peak
input int    InpStagnationSeconds        = 25;         // Exit if no new high/low in N seconds
input int    InpMinProfitToRetracePoints = 50;         // Min profit advance (50 pts = $0.50) before trailing retracement activates
input int    InpRetracementExitPoints    = 80;         // Exit if price gives back N points from peak (80 pts = $0.80)
input int    InpEmergencySL_Points       = 120;        // Emergency disaster SL (120 pts = $1.20 from closing quote)
input int    InpCooldownSeconds          = 15;         // Cooldown seconds after closing a basket

input group "=== Multi-Timeframe Candle Clock ==="
input bool   InpUseM5CandleClock      = true;       // Enable M5 Candle Clock filter
input int    InpMaxM5ElapsedSec       = 180;        // Max elapsed seconds in M5 bar to ignite (0-180s)

input group "=== Execution Guards ==="
input int    InpMaxSpreadPoints       = 30;         // Maximum allowed spread in points (3.0 pips)
input int    InpSessionStartHour      = 7;          // Trading Session Start Hour (Server time)
input int    InpSessionEndHour        = 22;         // Trading Session End Hour (Server time)
input ulong  InpMagicNumber           = 20261007;   // Strategy Magic Number

//+------------------------------------------------------------------+
//| Tick Ring Buffer Structure                                       |
//+------------------------------------------------------------------+
struct TickRecord
{
   long   time_msc;
   double bid;
   double ask;
};

#define TICK_BUF_CAPACITY 512
TickRecord g_tick_buf[TICK_BUF_CAPACITY];
int g_tick_head  = 0;
int g_tick_count = 0;

//+------------------------------------------------------------------+
//| Strategy State Machine                                           |
//+------------------------------------------------------------------+
enum ENUM_BASKET_DIR
{
   BASKET_NONE = 0,
   BASKET_BUY  = 1,
   BASKET_SELL = 2
};

ENUM_BASKET_DIR g_basket_dir   = BASKET_NONE;
int             g_basket_tier  = 0;            // 0: none, 1: tier1, 2: tier2, 3: tier3
int             g_peak_velocity = 0;
double          g_peak_price   = 0.0;
long            g_peak_time_msc = 0;
long            g_last_addon_msc = 0;
long            g_cooldown_until_msc = 0;
long            g_basket_open_time_msc = 0;
double          g_tier1_open_price = 0.0;
double          g_tier2_open_price = 0.0;

CTrade g_trade;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetDeviationInPoints(10);
   
   // Configure Broker Order Filling Mode
   uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)
      g_trade.SetTypeFilling(ORDER_FILLING_FOK);
   else if((filling & SYMBOL_FILLING_IOC) != 0)
      g_trade.SetTypeFilling(ORDER_FILLING_IOC);
   else
      g_trade.SetTypeFilling(ORDER_FILLING_RETURN);
      
   // Array g_tick_buf is statically sized with TICK_BUF_CAPACITY
   g_tick_head  = 0;
   g_tick_count = 0;
   
   ResetBasketState();
   
   PrintFormat("[INIT] MCP_VolumePulse_Sajim initialized on %s. Magic=%I64u", _Symbol, InpMagicNumber);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Comment("");
   PrintFormat("[DEINIT] MCP_VolumePulse_Sajim stopped. Reason=%d", reason);
}

//+------------------------------------------------------------------+
//| Reset Basket Tracking State                                      |
//+------------------------------------------------------------------+
void ResetBasketState()
{
   g_basket_dir       = BASKET_NONE;
   g_basket_tier      = 0;
   g_peak_velocity    = 0;
   g_peak_price       = 0.0;
   g_peak_time_msc    = 0;
   g_last_addon_msc   = 0;
   g_basket_open_time_msc = 0;
   g_tier1_open_price = 0.0;
   g_tier2_open_price = 0.0;
}

//+------------------------------------------------------------------+
//| Add incoming tick to rolling ring buffer                         |
//+------------------------------------------------------------------+
void BufferAddTick(const MqlTick &tick)
{
   g_tick_buf[g_tick_head].time_msc = tick.time_msc;
   g_tick_buf[g_tick_head].bid      = tick.bid;
   g_tick_buf[g_tick_head].ask      = tick.ask;
   
   g_tick_head = (g_tick_head + 1) % TICK_BUF_CAPACITY;
   if(g_tick_count < TICK_BUF_CAPACITY)
      g_tick_count++;
}

//+------------------------------------------------------------------+
//| Calculate Tick Velocity and Micro-Aggression Direction           |
//+------------------------------------------------------------------+
void CalculateVelocityMetrics(long current_msc, int &out_velocity, double &out_uptick_ratio)
{
   out_velocity     = 0;
   out_uptick_ratio = 0.5;
   
   if(g_tick_count < 2) return;
   
   int upticks   = 0;
   int downticks = 0;
   
   int cur_idx = (g_tick_head - 1 + TICK_BUF_CAPACITY) % TICK_BUF_CAPACITY;
   
   for(int i = 0; i < g_tick_count; i++)
   {
      long tick_time = g_tick_buf[cur_idx].time_msc;
      if(current_msc > tick_time && (current_msc - tick_time) > (long)InpVelocityWindow_ms)
         break;
         
      out_velocity++;
      
      int prev_idx = (cur_idx - 1 + TICK_BUF_CAPACITY) % TICK_BUF_CAPACITY;
      if(i + 1 < g_tick_count)
      {
         double diff = g_tick_buf[cur_idx].bid - g_tick_buf[prev_idx].bid;
         if(diff > 0.0) upticks++;
         else if(diff < 0.0) downticks++;
      }
      
      cur_idx = prev_idx;
   }
   
   int total_dir = upticks + downticks;
   if(total_dir > 0)
      out_uptick_ratio = (double)upticks / (double)total_dir;
}

//+------------------------------------------------------------------+
//| Validate Session and Candle Clock Filters                        |
//+------------------------------------------------------------------+
bool IsTimingAllowed(datetime tick_time)
{
   MqlDateTime dt;
   TimeToStruct(tick_time, dt);
   
   // 1. Session hours check
   if(dt.hour < InpSessionStartHour || dt.hour >= InpSessionEndHour)
      return false;
      
   // 2. M5 Candle Clock check: Only ignite during early expansion
   if(InpUseM5CandleClock)
   {
      datetime m5_open = iTime(_Symbol, PERIOD_M5, 0);
      if(m5_open > 0)
      {
         int elapsed = (int)(tick_time - m5_open);
         if(elapsed > InpMaxM5ElapsedSec)
            return false;
      }
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| Count active positions belonging to this strategy                |
//+------------------------------------------------------------------+
int CountOpenPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
            PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         {
            count++;
         }
      }
   }
   return count;
}

//+------------------------------------------------------------------+
//| Close all positions belonging to this strategy simultaneously    |
//+------------------------------------------------------------------+
void CloseBasket(string reason)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
            PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         {
            g_trade.PositionClose(ticket);
         }
      }
   }
   
   PrintFormat("[EXIT] Closed Basket. Reason: %s | Peak Velocity: %d", reason, g_peak_velocity);
   
   MqlTick last_tick;
   SymbolInfoTick(_Symbol, last_tick);
   g_cooldown_until_msc = last_tick.time_msc + (long)(InpCooldownSeconds * 1000);
   
   ResetBasketState();
}

//+------------------------------------------------------------------+
//| Main OnTick Execution Handler                                    |
//+------------------------------------------------------------------+
void OnTick()
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return;
   
   // 1. Buffer update
   BufferAddTick(tick);
   
   // 2. Calculate Velocity & Aggression metrics
   int    cur_velocity = 0;
   double uptick_ratio = 0.5;
   CalculateVelocityMetrics(tick.time_msc, cur_velocity, uptick_ratio);
   
   // 3. Reconcile Basket State with actual open positions
   int open_positions = CountOpenPositions();
   if(open_positions == 0 && g_basket_dir != BASKET_NONE)
   {
      ResetBasketState();
   }
   
   // 4. Spread filter check
   int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   
   // Display active telemetry HUD on chart
   string status_str = (g_basket_dir == BASKET_BUY) ? "LONG BASKET" : 
                       (g_basket_dir == BASKET_SELL) ? "SHORT BASKET" : "IDLE";
   Comment(StringFormat(
      "=== MCP_VolumePulse_Sajim ===\n"
      "Status: %s (Tier %d/%d) | Open Pos: %d\n"
      "Tick Velocity: %d ticks / %d ms (Peak: %d)\n"
      "Micro-Delta: %.1f%% Bulls\n"
      "Spread: %d pts (Max: %d)\n"
      "Peak Price: %.2f",
      status_str, g_basket_tier, 3, open_positions,
      cur_velocity, InpVelocityWindow_ms, g_peak_velocity,
      uptick_ratio * 100.0, spread, InpMaxSpreadPoints, g_peak_price
   ));
   
   //+---------------------------------------------------------------+
   //| SECTION A: ACTIVE BASKET MONITORING & EXIT CONDITIONS         |
   //+---------------------------------------------------------------+
   if(g_basket_dir != BASKET_NONE && open_positions > 0)
   {
      // Update Peak Velocity
      if(cur_velocity > g_peak_velocity)
         g_peak_velocity = cur_velocity;
         
      // Update Peak Price & Peak Timestamp
      if(g_basket_dir == BASKET_BUY)
      {
         if(tick.bid > g_peak_price)
         {
            g_peak_price    = tick.bid;
            g_peak_time_msc = tick.time_msc;
         }
      }
      else if(g_basket_dir == BASKET_SELL)
      {
         if(tick.ask < g_peak_price || g_peak_price == 0.0)
         {
            g_peak_price    = tick.ask;
            g_peak_time_msc = tick.time_msc;
         }
      }
      
      // Check Exit 1: Velocity Deceleration (Drying up)
      // Only active after InpMinHoldSeconds grace period to let the candle expansion develop
      long elapsed_basket_ms = tick.time_msc - g_basket_open_time_msc;
      if(elapsed_basket_ms >= ((long)InpMinHoldSeconds * 1000L) && g_peak_velocity >= InpVelocityTrigger)
      {
         double threshold = (double)g_peak_velocity * InpDecelExitRatio;
         if(cur_velocity < threshold)
         {
            CloseBasket("Velocity Exhaustion (Volume Dropped)");
            return;
         }
      }
      
      // Check Exit 2: Stagnation Clock (Price halted / failed to advance)
      if(tick.time_msc > g_peak_time_msc && 
         (tick.time_msc - g_peak_time_msc) >= ((long)InpStagnationSeconds * 1000L))
      {
         CloseBasket("Price Stagnation (Momentum Stalled)");
         return;
      }
      
      // Check Exit 3: Dynamic Profit Retracement Trailing
      // Only active if the pulse has moved at least InpMinProfitToRetracePoints into profit
      double min_profit_dist = (double)InpMinProfitToRetracePoints * _Point;
      double retrace_dist    = (double)InpRetracementExitPoints * _Point;
      
      if(g_basket_dir == BASKET_BUY)
      {
         bool reached_profit = (g_peak_price >= (g_tier1_open_price + min_profit_dist));
         if(reached_profit && tick.bid <= (g_peak_price - retrace_dist))
         {
            CloseBasket("Trailing Profit Retracement from Peak");
            return;
         }
      }
      else if(g_basket_dir == BASKET_SELL)
      {
         bool reached_profit = (g_peak_price <= (g_tier1_open_price - min_profit_dist));
         if(reached_profit && tick.ask >= (g_peak_price + retrace_dist))
         {
            CloseBasket("Trailing Profit Retracement from Peak");
            return;
         }
      }
      
      //+------------------------------------------------------------+
      //| SECTION B: PYRAMIDING SCALE-IN LOGIC                       |
      //+------------------------------------------------------------+
      long min_addon_gap = (long)InpMinAddonIntervalSec * 1000L;
      double addon_dist   = (double)InpMinAddonPoints * _Point;
      
      if(tick.time_msc - g_last_addon_msc >= min_addon_gap && cur_velocity >= InpVelocityContinuation)
      {
         // Tier 2 Scale-in (0.07 lot)
         if(g_basket_tier == 1)
         {
            if(g_basket_dir == BASKET_BUY && tick.ask >= (g_tier1_open_price + addon_dist))
            {
               double sl = (InpEmergencySL_Points > 0) ? NormalizeDouble(tick.bid - (InpEmergencySL_Points * _Point), _Digits) : 0;
               if(g_trade.Buy(InpLot_Tier2, _Symbol, tick.ask, sl, 0, "VolumePulse_T2"))
               {
                  g_basket_tier      = 2;
                  g_tier2_open_price = tick.ask;
                  g_last_addon_msc   = tick.time_msc;
                  PrintFormat("[PYRAMID] Tier 2 BUY 0.07 executed at %.2f", tick.ask);
               }
            }
            else if(g_basket_dir == BASKET_SELL && tick.bid <= (g_tier1_open_price - addon_dist))
            {
               double sl = (InpEmergencySL_Points > 0) ? NormalizeDouble(tick.ask + (InpEmergencySL_Points * _Point), _Digits) : 0;
               if(g_trade.Sell(InpLot_Tier2, _Symbol, tick.bid, sl, 0, "VolumePulse_T2"))
               {
                  g_basket_tier      = 2;
                  g_tier2_open_price = tick.bid;
                  g_last_addon_msc   = tick.time_msc;
                  PrintFormat("[PYRAMID] Tier 2 SELL 0.07 executed at %.2f", tick.bid);
               }
            }
         }
         // Tier 3 Scale-in (0.03 lot)
         else if(g_basket_tier == 2)
         {
            if(g_basket_dir == BASKET_BUY && tick.ask >= (g_tier2_open_price + addon_dist))
            {
               double sl = (InpEmergencySL_Points > 0) ? NormalizeDouble(tick.bid - (InpEmergencySL_Points * _Point), _Digits) : 0;
               if(g_trade.Buy(InpLot_Tier3, _Symbol, tick.ask, sl, 0, "VolumePulse_T3"))
               {
                  g_basket_tier    = 3;
                  g_last_addon_msc = tick.time_msc;
                  PrintFormat("[PYRAMID] Tier 3 BUY 0.03 executed at %.2f", tick.ask);
               }
            }
            else if(g_basket_dir == BASKET_SELL && tick.bid <= (g_tier2_open_price - addon_dist))
            {
               double sl = (InpEmergencySL_Points > 0) ? NormalizeDouble(tick.ask + (InpEmergencySL_Points * _Point), _Digits) : 0;
               if(g_trade.Sell(InpLot_Tier3, _Symbol, tick.bid, sl, 0, "VolumePulse_T3"))
               {
                  g_basket_tier    = 3;
                  g_last_addon_msc = tick.time_msc;
                  PrintFormat("[PYRAMID] Tier 3 SELL 0.03 executed at %.2f", tick.bid);
               }
            }
         }
      }
      return;
   }
   
   //+---------------------------------------------------------------+
   //| SECTION C: IGNITION ENTRY DETECTION (IDLE STATE)             |
   //+---------------------------------------------------------------+
   if(g_basket_dir == BASKET_NONE && open_positions == 0)
   {
      // 1. Check Cooldown
      if(tick.time_msc < g_cooldown_until_msc) return;
      
      // 2. Check Spread Guard
      if(spread > InpMaxSpreadPoints) return;
      
      // 3. Check Session & M5 Candle Clock Timing
      if(!IsTimingAllowed(tick.time)) return;
      
      // 4. Check Ignition Velocity Trigger
      if(cur_velocity < InpVelocityTrigger) return;
      
      // 5. Directional Aggression Check
      // Bullish Ignition Trigger
      if(uptick_ratio >= InpDirectionThreshold)
      {
         double sl = (InpEmergencySL_Points > 0) ? NormalizeDouble(tick.bid - (InpEmergencySL_Points * _Point), _Digits) : 0;
         if(g_trade.Buy(InpLot_Tier1, _Symbol, tick.ask, sl, 0, "VolumePulse_T1"))
         {
            g_basket_dir       = BASKET_BUY;
            g_basket_tier      = 1;
            g_peak_velocity    = cur_velocity;
            g_peak_price       = tick.bid;
            g_peak_time_msc    = tick.time_msc;
            g_last_addon_msc   = tick.time_msc;
            g_basket_open_time_msc = tick.time_msc;
            g_tier1_open_price = tick.ask;
            PrintFormat("[IGNITION] BUY 0.10 executed. Velocity=%d, Aggression=%.1f%%", cur_velocity, uptick_ratio * 100.0);
         }
      }
      // Bearish Ignition Trigger
      else if(uptick_ratio <= (1.0 - InpDirectionThreshold))
      {
         double sl = (InpEmergencySL_Points > 0) ? NormalizeDouble(tick.ask + (InpEmergencySL_Points * _Point), _Digits) : 0;
         if(g_trade.Sell(InpLot_Tier1, _Symbol, tick.bid, sl, 0, "VolumePulse_T1"))
         {
            g_basket_dir       = BASKET_SELL;
            g_basket_tier      = 1;
            g_peak_velocity    = cur_velocity;
            g_peak_price       = tick.ask;
            g_peak_time_msc    = tick.time_msc;
            g_last_addon_msc   = tick.time_msc;
            g_basket_open_time_msc = tick.time_msc;
            g_tier1_open_price = tick.bid;
            PrintFormat("[IGNITION] SELL 0.10 executed. Velocity=%d, Aggression=%.1f%%", cur_velocity, (1.0 - uptick_ratio) * 100.0);
         }
      }
   }
}
//+------------------------------------------------------------------+
