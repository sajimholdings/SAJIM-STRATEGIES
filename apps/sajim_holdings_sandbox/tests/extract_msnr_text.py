import pypdf
from pathlib import Path

pdf_path = str(__import__("pathlib").Path(__file__).resolve().parents[3] / r"Alchemist MSNR (1).pdf")
reader = pypdf.PdfReader(pdf_path)

print(f"Total pages: {len(reader.pages)}")

extracted_text = []
for i, page in enumerate(reader.pages):
    t = page.extract_text() or ""
    extracted_text.append(f"=== PAGE {i+1} ===\n{t}\n")
    print(f"Page {i+1}: length {len(t)} chars")

out_path = Path(__file__).resolve().parent / "alchemist_msnr_extracted_text.txt"
with open(out_path, "w", encoding="utf-8") as f:
    f.writelines(extracted_text)

print(f"\nFull text saved to {out_path}")
