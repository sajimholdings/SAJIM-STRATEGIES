import fitz  # PyMuPDF
from pathlib import Path

doc = fitz.open(str(__import__("pathlib").Path(__file__).resolve().parents[3] / r"Alchemist MSNR (1).pdf"))
print(f"Total pages: {len(doc)}")

# Check each page for text, blocks, drawings
for i in range(len(doc)):
    page = doc[i]
    text = page.get_text("text")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    print(f"--- Page {i+1} ({len(lines)} lines) ---")
    for l in lines[:10]:
        try:
            print(f"  {l}")
        except Exception:
            pass
