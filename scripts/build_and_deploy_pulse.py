import subprocess
import shutil
from pathlib import Path
import sys

src = Path(r"f:\Tete\SAJIM STRATEGIES\strategies\MCP_VolumePulse_Sajim\MCP_VolumePulse_Sajim.mq5")
term_mq5 = Path(r"C:\Users\sajim\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Experts\Sajim\MCP_VolumePulse_Sajim.mq5")
term_ex5 = term_mq5.with_suffix(".ex5")
term_log = term_mq5.with_suffix(".log")
repo_ex5 = src.with_suffix(".ex5")

# Copy MQ5
shutil.copy2(src, term_mq5)
print("Copied MQ5 to terminal folder")

# Compile
editor = r"C:\Program Files\MetaTrader 5\metaeditor64.exe"
res = subprocess.run([editor, f"/compile:{term_mq5}", f"/log:{term_log}"], capture_output=True)
print("Compiler return code:", res.returncode)

if term_log.exists():
    with open(term_log, "r", encoding="utf-16le", errors="ignore") as f:
        log_txt = f.read()
        lines = [l.strip() for l in log_txt.splitlines() if l.strip()]
        for l in lines[-5:]:
            print("  Log:", l)

if term_ex5.exists():
    shutil.copy2(term_ex5, repo_ex5)
    print("Copied EX5 back to repo. Size:", term_ex5.stat().st_size)
else:
    print("ERROR: EX5 was not generated!")

# Update chart01.chr
chr_path = Path(r"C:\Users\sajim\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Profiles\Charts\Default\chart01.chr")
if chr_path.exists():
    with open(chr_path, "r", encoding="utf-16le") as f:
        chr_txt = f.read()
    
    if "InpMinHoldSeconds" not in chr_txt:
        chr_txt = chr_txt.replace(
            "InpDecelExitRatio=0.35",
            "InpMinHoldSeconds=12\nInpDecelExitRatio=0.35"
        )
    chr_txt = chr_txt.replace("InpStagnationSeconds=10", "InpStagnationSeconds=15")
    
    with open(chr_path, "w", encoding="utf-16le") as f:
        f.write(chr_txt)
    print("Updated chart01.chr successfully")
