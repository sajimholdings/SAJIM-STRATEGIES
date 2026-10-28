from pathlib import Path
import subprocess
import shutil

mq5_path = Path(r"f:\Tete\SAJIM STRATEGIES\strategies\MCP_VolumePulse_Sajim\MCP_VolumePulse_Sajim.mq5")
with open(mq5_path, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Update inputs
old_inputs = '''input group "=== Dynamic Volume-Drop & Exit ==="
input int    InpMinHoldSeconds        = 12;         // Grace period: min seconds before velocity exhaustion exit
input double InpDecelExitRatio        = 0.35;       // Exit when velocity drops below this % of peak
input int    InpStagnationSeconds     = 15;         // Exit if no new high/low in N seconds
input int    InpRetracementExitPoints = 35;         // Exit if price gives back N points from peak (0.35)
input int    InpEmergencySL_Points    = 80;         // Emergency disaster SL (80 pts = $0.80 from closing quote)
input int    InpCooldownSeconds       = 15;         // Cooldown seconds after closing a basket'''

new_inputs = '''input group "=== Dynamic Volume-Drop & Exit ==="
input int    InpMinHoldSeconds           = 15;         // Grace period: min seconds before velocity exhaustion exit
input double InpDecelExitRatio           = 0.35;       // Exit when velocity drops below this % of peak
input int    InpStagnationSeconds        = 25;         // Exit if no new high/low in N seconds
input int    InpMinProfitToRetracePoints = 50;         // Min profit advance (50 pts = $0.50) before trailing retracement activates
input int    InpRetracementExitPoints    = 80;         // Exit if price gives back N points from peak (80 pts = $0.80)
input int    InpEmergencySL_Points       = 120;        // Emergency disaster SL (120 pts = $1.20 from closing quote)
input int    InpCooldownSeconds          = 15;         // Cooldown seconds after closing a basket'''

code = code.replace(old_inputs, new_inputs)

# 2. Update Exit 3 logic
old_exit_3 = '''      // Check Exit 3: Micro-Retracement from Peak Price
      double retrace_dist = (double)InpRetracementExitPoints * _Point;
      if(g_basket_dir == BASKET_BUY && tick.bid <= (g_peak_price - retrace_dist))
      {
         CloseBasket("Micro-Retracement from Pulse Peak");
         return;
      }
      else if(g_basket_dir == BASKET_SELL && tick.ask >= (g_peak_price + retrace_dist))
      {
         CloseBasket("Micro-Retracement from Pulse Peak");
         return;
      }'''

new_exit_3 = '''      // Check Exit 3: Dynamic Profit Retracement Trailing
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
      }'''

code = code.replace(old_exit_3, new_exit_3)

with open(mq5_path, "w", encoding="utf-8") as f:
    f.write(code)

print("Updated MQ5 with loosened exit rules")

# 3. Copy MQ5 and compile
term_mq5 = Path(r"C:\Users\sajim\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Experts\Sajim\MCP_VolumePulse_Sajim.mq5")
term_ex5 = term_mq5.with_suffix(".ex5")
term_log = term_mq5.with_suffix(".log")
repo_ex5 = mq5_path.with_suffix(".ex5")

shutil.copy2(mq5_path, term_mq5)

editor = r"C:\Program Files\MetaTrader 5\metaeditor64.exe"
res = subprocess.run([editor, f"/compile:{term_mq5}", f"/log:{term_log}"], capture_output=True)

if term_log.exists():
    with open(term_log, "r", encoding="utf-16le", errors="ignore") as f:
        log_txt = f.read()
        lines = [l.strip() for l in log_txt.splitlines() if l.strip()]
        for l in lines[-4:]:
            print("  Log:", l)

if term_ex5.exists():
    shutil.copy2(term_ex5, repo_ex5)
    print("Copied EX5 back to repo. Size:", term_ex5.stat().st_size)
else:
    print("ERROR: EX5 was not generated!")

# 4. Update chart01.chr
chr_path = Path(r"C:\Users\sajim\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Profiles\Charts\Default\chart01.chr")
if chr_path.exists():
    with open(chr_path, "r", encoding="utf-16le") as f:
        chr_txt = f.read()
    
    chr_txt = chr_txt.replace("InpMinHoldSeconds=12", "InpMinHoldSeconds=15")
    chr_txt = chr_txt.replace("InpStagnationSeconds=15", "InpStagnationSeconds=25")
    chr_txt = chr_txt.replace("InpRetracementExitPoints=35", "InpRetracementExitPoints=80")
    chr_txt = chr_txt.replace("InpEmergencySL_Points=80", "InpEmergencySL_Points=120")
    if "InpMinProfitToRetracePoints" not in chr_txt:
        chr_txt = chr_txt.replace(
            "InpRetracementExitPoints=80",
            "InpMinProfitToRetracePoints=50\nInpRetracementExitPoints=80"
        )
    
    with open(chr_path, "w", encoding="utf-16le") as f:
        f.write(chr_txt)
    print("Updated chart01.chr successfully")
