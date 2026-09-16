from pathlib import Path

p = Path("../data/dukascopy")
if not p.exists():
    print("Directory not found")
    exit()

dirs = [d for d in sorted(p.iterdir()) if d.is_dir()]
total_files = 0
total_bytes = 0

print(f"{'Symbol':<10} {'Files':<8} {'Timeframes':<30} {'Size (MB)':<10}")
print("-" * 62)

for d in dirs:
    csvs = sorted(d.glob("*.csv"))
    tfs = ", ".join(f.stem for f in csvs)
    size_mb = sum(f.stat().st_size for f in csvs) / 1e6
    total_files += len(csvs)
    total_bytes += sum(f.stat().st_size for f in csvs)
    print(f"{d.name:<10} {len(csvs):<8} {tfs:<30} {size_mb:>8.1f}")

print("-" * 62)
print(f"Total: {total_files} files across {len(dirs)} symbols ({total_bytes / 1e6:.1f} MB)")
