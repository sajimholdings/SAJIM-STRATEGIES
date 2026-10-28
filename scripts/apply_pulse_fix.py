from pathlib import Path

mq5_path = Path(r"f:\Tete\SAJIM STRATEGIES\strategies\MCP_VolumePulse_Sajim\MCP_VolumePulse_Sajim.mq5")
with open(mq5_path, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Add InpMinHoldSeconds input
code = code.replace(
    'input double InpDecelExitRatio        = 0.35;',
    'input int    InpMinHoldSeconds        = 12;         // Grace period: min seconds before velocity exhaustion exit\ninput double InpDecelExitRatio        = 0.35;'
)
code = code.replace(
    'input int    InpStagnationSeconds     = 10;',
    'input int    InpStagnationSeconds     = 15;'
)

# 2. Add g_basket_open_time_msc variable
code = code.replace(
    'double          g_tier1_open_price = 0.0;',
    'long            g_basket_open_time_msc = 0;\ndouble          g_tier1_open_price = 0.0;'
)

# 3. Add to ResetBasketState
code = code.replace(
    '   g_tier1_open_price = 0.0;',
    '   g_basket_open_time_msc = 0;\n   g_tier1_open_price = 0.0;'
)

# 4. Update Exit 1 to check elapsed time
old_exit_1 = '''      // Check Exit 1: Velocity Deceleration (Drying up)
      if(g_peak_velocity >= InpVelocityTrigger)
      {
         double threshold = (double)g_peak_velocity * InpDecelExitRatio;
         if(cur_velocity < threshold)
         {
            CloseBasket("Velocity Exhaustion (Volume Dropped)");
            return;
         }
      }'''

new_exit_1 = '''      // Check Exit 1: Velocity Deceleration (Drying up)
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
      }'''

code = code.replace(old_exit_1, new_exit_1)

# 5. Record g_basket_open_time_msc on BUY and SELL ignition
old_buy_ign = '''            g_last_addon_msc   = tick.time_msc;
            g_tier1_open_price = tick.ask;
            PrintFormat("[IGNITION] BUY 0.10 executed.'''

new_buy_ign = '''            g_last_addon_msc   = tick.time_msc;
            g_basket_open_time_msc = tick.time_msc;
            g_tier1_open_price = tick.ask;
            PrintFormat("[IGNITION] BUY 0.10 executed.'''

old_sell_ign = '''            g_last_addon_msc   = tick.time_msc;
            g_tier1_open_price = tick.bid;
            PrintFormat("[IGNITION] SELL 0.10 executed.'''

new_sell_ign = '''            g_last_addon_msc   = tick.time_msc;
            g_basket_open_time_msc = tick.time_msc;
            g_tier1_open_price = tick.bid;
            PrintFormat("[IGNITION] SELL 0.10 executed.'''

code = code.replace(old_buy_ign, new_buy_ign)
code = code.replace(old_sell_ign, new_sell_ign)

with open(mq5_path, "w", encoding="utf-8") as f:
    f.write(code)

print("Updated MQ5 successfully")
