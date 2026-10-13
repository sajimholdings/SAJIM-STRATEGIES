import pypdf
import re

pdf_path = str(__import__("pathlib").Path(__file__).resolve().parents[3] / r"Alch100Complete-compressed.pdf")
reader = pypdf.PdfReader(pdf_path)
print(f"Total pages: {len(reader.pages)}")

# Keywords to search for algorithmic or trading strategies
keywords = [
    "strategy", "indicator", "moving average", "ema", "rsi", "macd",
    "backtest", "entry", "exit", "stop loss", "take profit", "alch100",
    "algorithm", "quantitative", "trading system", "rule", "model",
    "breakout", "reversal", "momentum", "trend", "dow", "elliott"
]

findings = []

for idx, page in enumerate(reader.pages):
    text = page.extract_text() or ""
    lower_text = text.lower()
    matches = [kw for kw in keywords if kw in lower_text]
    if matches:
        findings.append((idx + 1, matches, text[:300].replace("\n", " ")))

print(f"Pages with keyword hits: {len(findings)}")
for page_num, matches, snippet in findings:
    print(f"Page {page_num:2d} | Hits: {', '.join(matches):<35} | Snippet: {snippet[:120]}...")
