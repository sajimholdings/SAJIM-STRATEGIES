import shutil
from pathlib import Path

chart_path = Path(r"C:\Users\sajim\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Profiles\Charts\Default\chart01.chr")
backup_path = chart_path.with_suffix(".chr.bak2")
shutil.copy2(chart_path, backup_path)
print(f"Backed up {chart_path} -> {backup_path}")

content = chart_path.read_text(encoding="utf-16le", errors="ignore")

# Replace timeframe: period_size=5 -> period_size=1 (M1)
content = content.replace("period_size=5", "period_size=1")

# Replace expert section
old_expert_start = content.find("<expert>")
old_expert_end = content.find("</expert>") + len("</expert>")

new_expert = """<expert>
name=MCP_VolumePulse_Sajim
path=Experts\\Sajim\\MCP_VolumePulse_Sajim.ex5
expertmode=5
<inputs>
=== Pyramiding Position Sizing ====
InpLot_Tier1=0.1
InpLot_Tier2=0.07
InpLot_Tier3=0.03
InpMinAddonPoints=10
InpMinAddonIntervalSec=3
=== Volume & Velocity Engine ====
InpVelocityWindow_ms=3000
InpVelocityTrigger=20
InpVelocityContinuation=14
InpDirectionThreshold=0.7
=== Dynamic Volume-Drop & Exit ====
InpDecelExitRatio=0.35
InpStagnationSeconds=10
InpRetracementExitPoints=15
InpEmergencySL_Points=35
InpCooldownSeconds=15
=== Multi-Timeframe Candle Clock ====
InpUseM5CandleClock=true
InpMaxM5ElapsedSec=180
=== Execution Guards ====
InpMaxSpreadPoints=50
InpSessionStartHour=0
InpSessionEndHour=24
InpMagicNumber=20261007
</inputs>
</expert>"""

if old_expert_start != -1 and old_expert_end != -1:
    content = content[:old_expert_start] + new_expert + content[old_expert_end:]
    print("Replaced existing expert section.")
else:
    # insert before <window>
    w_idx = content.find("<window>")
    if w_idx != -1:
        content = content[:w_idx] + new_expert + "\n\n" + content[w_idx:]
        print("Inserted new expert section before <window>.")
    else:
        content = content + "\n\n" + new_expert
        print("Appended expert section.")

chart_path.write_text(content, encoding="utf-16le")
print("Successfully updated chart01.chr to MCP_VolumePulse_Sajim on XAUUSD M1!")
