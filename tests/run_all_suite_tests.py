"""
SAJIM Quant Platform - Master Test Suite Runner
Executes all automated tests across Phase 1, Phase 2, Phase 3, Phase 4,
and all cross-folder verification scripts.
"""

import os
import sys
import subprocess
from pathlib import Path

workspace_root = Path(__file__).resolve().parent.parent
venv_python = workspace_root / "files" / ".venv" / "Scripts" / "python.exe"
global_python = sys.executable

TEST_SCRIPTS = [
    ("tests/test_phase1.py", global_python),
    ("tests/test_phase2_python_adapter.py", str(venv_python) if venv_python.exists() else global_python),
    ("tests/test_phase3_mt5_adapter.py", global_python),
    ("tests/test_pine_importer.py", global_python),
    ("tests/test_portfolio_analytics.py", global_python),
    ("tests/test_external_systems_ingest.py", global_python),
    ("tests/test_deriv_updown.py", global_python),
    ("tests/test_backtrader_sunrise.py", str(venv_python) if venv_python.exists() else global_python),
    ("tests/test_smc_daemon_replay.py", global_python),
    ("tests/test_gold_pullback_ai_replay.py", global_python),
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
