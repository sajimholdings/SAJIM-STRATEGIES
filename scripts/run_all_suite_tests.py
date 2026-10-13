"""
SAJIM Quant Platform - Master runner for the backtest scripts in scripts/.
These need market data under data/ (see tools/data_downloader). The unit tests live in
tests/ and run with plain `pytest`. Prefer `sajim run-all`, which also re-exports the dashboard.
"""

import os
import sys
import subprocess
from pathlib import Path

workspace_root = Path(__file__).resolve().parent.parent
global_python = sys.executable

TEST_SCRIPTS = [
    ("scripts/run_phase1_schema_check.py", global_python),
    ("scripts/run_phase2_python_adapter.py", global_python),
    ("scripts/run_phase3_mt5_adapter.py", global_python),
    ("scripts/run_pine_importer.py", global_python),
    ("scripts/run_portfolio_analytics.py", global_python),
    ("scripts/run_external_systems_ingest.py", global_python),
    ("scripts/run_deriv_updown.py", global_python),
    ("scripts/run_backtrader_sunrise.py", global_python),
    ("scripts/run_smc_daemon_replay.py", global_python),
    ("scripts/run_gold_pullback_ai_replay.py", global_python),
]

def run_all():
    print("=" * 70, flush=True)
    print("  SAJIM QUANT PLATFORM — MASTER SUITE VERIFICATION", flush=True)
    print("=" * 70, flush=True)
    
    passed = 0
    failed = 0
    
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    
    for script, py_exec in TEST_SCRIPTS:
        script_path = workspace_root / script
        if not script_path.exists():
            print(f"[-] SKIPPED: {script} (not found)", flush=True)
            continue
            
        print(f"\n[>] RUNNING: {script} with {Path(py_exec).parent.name}/{Path(py_exec).name} ...", flush=True)
        try:
            res = subprocess.run(
                [py_exec, str(script_path)],
                cwd=str(workspace_root),
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
            
            if res.returncode == 0:
                print(f"[+] PASSED: {script}", flush=True)
                passed += 1
            else:
                print(f"[x] FAILED: {script} (exit code {res.returncode})", flush=True)
                if res.stdout:
                    print(f"STDOUT:\n{res.stdout[-400:]}", flush=True)
                if res.stderr:
                    print(f"STDERR:\n{res.stderr[-400:]}", flush=True)
                failed += 1
        except subprocess.TimeoutExpired:
            print(f"[!] TIMED OUT: {script}", flush=True)
            failed += 1

    print("\n" + "=" * 70, flush=True)
    print(f"  TEST SUITE RESULTS: {passed} PASSED, {failed} FAILED (TOTAL {passed + failed})", flush=True)
    print("=" * 70, flush=True)
    
    if failed > 0:
        sys.exit(1)

if __name__ == "__main__":
    run_all()
