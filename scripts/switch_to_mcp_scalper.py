import sys
from pathlib import Path

chr_path = Path(r"C:\Users\sajim\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Profiles\Charts\Default\chart01.chr")

with open(chr_path, "r", encoding="utf-16le") as f:
    text = f.read()

# 1. Update period size to M5
text = text.replace("period_size=1", "period_size=5")

# 2. Replace <expert> block
expert_start = text.find("<expert>")
expert_end = text.find("</expert>") + len("</expert>")

if expert_start != -1 and expert_end != -1:
    new_expert_block = """<expert>
name=MCP_Scalper_Sajim
path=Experts\\Sajim\\MCP_Scalper_Sajim.ex5
expertmode=5
<inputs>
=== Entry Logic ====
InpEMA_Trend=50
InpEMA_Signal=21
InpRSI_Period=14
InpRSI_Lower=40.0
InpRSI_Upper=60.0
=== Risk Management ====
InpLotSize=0.1
InpSL_ATR_Mult=1.0
InpTP_ATR_Mult=3.0
InpATR_Period=14
InpBreakeven_R=1.5
InpBE_Buffer_Points=10
InpTrailStart_ATR=0.0
InpTrailStep_ATR=1.0
=== Filters ====
InpUseHTF_Filter=true
InpHTF_Timeframe=16385
InpHTF_EMA_Period=100
InpMaxTradesPerDay=0
InpMaxSpreadPoints=50
InpSessionStart=0
InpSessionEnd=24
=== System ====
InpMagicNumber=20261006
</inputs>
</expert>"""
    text = text[:expert_start] + new_expert_block + text[expert_end:]

with open(chr_path, "w", encoding="utf-16le") as f:
    f.write(text)

print("Updated chart01.chr with MCP_Scalper_Sajim on M5")
