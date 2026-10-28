import subprocess
import shutil
from pathlib import Path

src = Path(r"f:\Tete\SAJIM STRATEGIES\strategies\MCP_VolumePulse_Sajim\MCP_VolumePulse_Sajim.mq5")
base = Path(r"C:\Users\sajim\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075")
dest_mq5_sajim = base / "MQL5" / "Experts" / "Sajim" / "MCP_VolumePulse_Sajim.mq5"
dest_mq5_root = base / "MQL5" / "Experts" / "MCP_VolumePulse_Sajim.mq5"

dest_mq5_sajim.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(src, dest_mq5_sajim)
shutil.copy2(src, dest_mq5_root)

editor = r"C:\Program Files\MetaTrader 5\metaeditor64.exe"
log_sajim = dest_mq5_sajim.with_suffix(".log")
log_root = dest_mq5_root.with_suffix(".log")
log_src = src.with_suffix(".log")

print("Compiling Sajim target...")
res1 = subprocess.run([editor, "/portable", f"/compile:{dest_mq5_sajim}", f"/log:{log_sajim}"], capture_output=True, text=True)
print("Return code:", res1.returncode)

if log_sajim.exists():
    try:
        content = log_sajim.read_text(encoding="utf-16")
    except Exception:
        content = log_sajim.read_text(encoding="utf-8", errors="ignore")
    print("--- Log Content ---")
    print(content)
else:
    print("Log file not found!")

ex5_sajim = dest_mq5_sajim.with_suffix(".ex5")
print("EX5 exists:", ex5_sajim.exists())
if ex5_sajim.exists():
    print("EX5 size:", ex5_sajim.stat().st_size)
    shutil.copy2(ex5_sajim, src.with_suffix(".ex5"))
    print("Copied EX5 to strategy repo:", src.with_suffix(".ex5").exists())
