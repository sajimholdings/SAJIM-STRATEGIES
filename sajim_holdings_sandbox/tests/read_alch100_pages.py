import pypdf

reader = pypdf.PdfReader(r"F:\Tete\SAJIM STRATEGIES\Alch100Complete-compressed.pdf")

print("--- PAGE 35 (Sean Russo) ---")
print(reader.pages[34].extract_text())

print("\n--- PAGE 36 ---")
print(reader.pages[35].extract_text())

print("\n--- PAGE 57 (Historical summaries) ---")
print(reader.pages[56].extract_text())
