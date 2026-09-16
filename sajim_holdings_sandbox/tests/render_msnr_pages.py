import fitz
from pathlib import Path

doc = fitz.open(r"F:\Tete\SAJIM STRATEGIES\Alchemist MSNR (1).pdf")
scratch_dir = Path(r"C:\Users\sajim\.gemini\antigravity-ide\brain\f68ae4be-7dbb-4c18-96fa-f20e63831ca2\scratch")
scratch_dir.mkdir(parents=True, exist_ok=True)

# Render key educational and chart pages
pages_to_render = [5, 7, 12, 13, 21, 23, 24, 29, 30, 39]
rendered_paths = []

for p_num in pages_to_render:
    page = doc[p_num - 1]
    pix = page.get_pixmap(dpi=150)
    out_img = scratch_dir / f"msnr_page_{p_num}.png"
    pix.save(str(out_img))
    rendered_paths.append(out_img)
    print(f"Rendered Page {p_num} -> {out_img}")
