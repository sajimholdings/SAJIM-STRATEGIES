"""
Phase 12: The Hermes Overlord
=============================
An autonomous AI Agent that reads the physical outputs of the BEEP MT5 
engine (playbook_data.csv) and generates structural insights to adjust 
the DNA and risk models dynamically.
Supports DeepSeek API (deepseek-chat) and Google Gemini.
"""

import sys
import os
import csv
import json
import requests

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# API Configuration — the key comes ONLY from the environment.
# NOTE: a hardcoded fallback key was removed from here (it silently burned the
# owner's paid API credits and was exposed in git history — rotate that key!).
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
if DEEPSEEK_API_KEY.startswith("sk-your_") or len(DEEPSEEK_API_KEY) < 20:
    DEEPSEEK_API_KEY = ""

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

def load_playbook_data(filepath):
    if not os.path.exists(filepath):
        print(f"[HERMES] Cannot find playbook data at {filepath}")
        return []
        
    trades = []
    with open(filepath, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(row)
    return trades

def build_prompt(trades):
    # Take the last 50 trades to avoid token limits and focus on recent market structure
    recent_trades = trades[-50:]
    
    prompt = f"""
You are Hermes, an elite, autonomous quantitative trading AI overlord.
You manage the 'BEEP' physics engine, which trades Forex, Crypto, and Indices based on Asymptotic Baselines, Mass, and Velocity.

Here is the exact structural log of the last {len(recent_trades)} trades executed by the physical MT5 bot:

"""
    for t in recent_trades:
        pnl_val = t.get('Profit') or t.get('PnL') or '0.0'
        vel_info = f"Vel(M1/M5/M15): {t.get('Vel_M1', '0')}/{t.get('Vel_M5', '0')}/{t.get('Vel_M15', '0')}" if 'Vel_M1' in t else f"Price: {t.get('Price', 'N/A')}"
        prompt += f"- [{t.get('Timestamp', 'N/A')}] {t.get('Symbol', 'N/A')} | Action: {t.get('Action', 'N/A')} | "
        prompt += f"Mass: {t.get('Mass', 'N/A')} | {vel_info} | "
        prompt += f"Z-Score: {t.get('Z_Score', 'N/A')} | Profit/PnL: {pnl_val}\n"
        
    prompt += """
YOUR DIRECTIVE:
1. Analyze this raw physical data. Look for exact correlations between Mass/Velocity and Profit/Loss.
2. Tell me exactly what the BEEP engine is doing wrong, and what it is doing right.
3. Give me 3 concrete, structural commands to adjust the DNA Thresholds (T1, T2) or the Death Avoidance Risk Scaler.

4. AUTONOMOUS MUTATION PAYLOAD:
At the very end of your response, output a valid JSON block enclosed in ```json ... ``` with your exact recommended evolved parameters:
```json
{
  "min_mass_forex": 15.0,
  "min_mass_indices_metals": 40.0,
  "death_avoidance_risk": {
    "XAUUSD": 0.010,
    "BTCUSD": 0.010,
    "US30": 0.010,
    "NDX100": 0.010,
    "GBPJPY": 0.020,
    "EURUSD": 0.020,
    "GBPUSD": 0.025
  },
  "dna_thresholds": {
    "XAUUSD": [2.5, 1.5],
    "GBPJPY": [4.0, 2.5],
    "EURUSD": [2.5, 1.5],
    "GBPUSD": [4.0, 2.5],
    "US30": [4.0, 2.5]
  },
  "notes": "Summary of changes."
}
```
"""
    return prompt

def apply_hermes_mutations(ai_response):
    """Parses JSON mutation payload from Hermes response and updates dna_config.json."""
    import re
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dna_config.json")
    if not os.path.exists(config_path):
        config_path = os.path.join(os.path.dirname(__file__), "dna_config.json")
        
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', ai_response, re.DOTALL)
    if not match:
        return
        
    try:
        new_params = json.loads(match.group(1))
        
        current_cfg = {}
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                current_cfg = json.load(f)
                
        version = current_cfg.get("version", 0) + 1
        current_cfg["version"] = version
        current_cfg["last_updated"] = time.strftime('%Y-%m-%d %H:%M:%S')
        
        if "min_mass_forex" in new_params:
            current_cfg["min_mass_forex"] = max(10.0, min(float(new_params["min_mass_forex"]), 50.0))
        if "min_mass_indices_metals" in new_params:
            current_cfg["min_mass_indices_metals"] = max(20.0, min(float(new_params["min_mass_indices_metals"]), 80.0))
        if "death_avoidance_risk" in new_params and isinstance(new_params["death_avoidance_risk"], dict):
            for k, v in new_params["death_avoidance_risk"].items():
                safe_risk = max(0.005, min(float(v), 0.030))
                current_cfg.setdefault("death_avoidance_risk", {})[k] = safe_risk
        if "dna_thresholds" in new_params and isinstance(new_params["dna_thresholds"], dict):
            for k, v in new_params["dna_thresholds"].items():
                if isinstance(v, (list, tuple)) and len(v) >= 2:
                    current_cfg.setdefault("dna_thresholds", {})[k] = [float(v[0]), float(v[1])]
        if "notes" in new_params:
            current_cfg["hermes_notes"] = str(new_params["notes"])
            
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(current_cfg, f, indent=2)
            
        print(f"\n[🧬 HERMES AUTONOMOUS MUTATION APPLIED] -> Updated dna_config.json to Version {version}")
        print(f"   The live MT5 bot will automatically hot-reload these evolved parameters on its next cycle.")
    except Exception as e:
        print(f"[HERMES MUTATION NOTICE] Could not auto-apply JSON parameters: {e}")

def call_deepseek(prompt, api_key):
    print("\n[HERMES] Ingesting playbook data. Connecting to DeepSeek Neural Network (deepseek-chat)...")
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "system", 
                "content": "You are Hermes, an elite autonomous quantitative trading AI overlord specializing in market physics, liquidity vacuums, and risk of ruin mathematics."
            },
            {
                "role": "user", 
                "content": prompt
            }
        ],
        "temperature": 0.3,
        "stream": False
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]

def call_gemini(prompt, api_key):
    print("\n[HERMES] Ingesting playbook data. Connecting to Gemini Neural Network...")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    response = requests.post(url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]

def call_hermes(prompt):
    ai_response = None
    
    # Priority 1: DeepSeek
    if DEEPSEEK_API_KEY and DEEPSEEK_API_KEY.strip():
        try:
            ai_response = call_deepseek(prompt, DEEPSEEK_API_KEY.strip())
        except Exception as e:
            print(f"[HERMES WARNING] DeepSeek request failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(e.response.text)
                
    # Priority 2: Gemini fallback
    if not ai_response and GEMINI_API_KEY and GEMINI_API_KEY.strip():
        try:
            ai_response = call_gemini(prompt, GEMINI_API_KEY.strip())
        except Exception as e:
            print(f"[HERMES FATAL ERROR] Failed to contact Neural Network: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(e.response.text)

    if ai_response:
        print("\n" + "="*60)
        print(" THE HERMES OVERLORD DIRECTIVE")
        print("="*60)
        print(ai_response)
        print("="*60 + "\n")
        apply_hermes_mutations(ai_response)
    else:
        print("\n[HERMES] Could not obtain directive. Please set DEEPSEEK_API_KEY.")
        print("\n[HERMES] Could not obtain directive. Please set DEEPSEEK_API_KEY.")

def sync_live_mt5_deals(filepath):
    try:
        import MetaTrader5 as mt5
        import datetime
        if not mt5.initialize():
            return
        deals = mt5.history_deals_get(datetime.datetime.now() - datetime.timedelta(days=7), datetime.datetime.now() + datetime.timedelta(days=1))
        if not deals:
            return
            
        rows = load_playbook_data(filepath)
        added = False
        for d in deals:
            if d.magic == 777012:
                dt_str = datetime.datetime.fromtimestamp(d.time).strftime('%Y-%m-%d %H:%M:%S')
                act = 'ENTRY' if d.entry == 0 else ('EXIT_TRAIL_SL' if 'sl' in str(d.comment).lower() else 'EXIT_MACRO' if 'macro' in str(d.comment).lower() else 'EXIT_DEAL')
                if not any(r.get('Timestamp') == dt_str and r.get('Symbol') == d.symbol and r.get('Action') == act for r in rows):
                    rows.append({
                        'Timestamp': dt_str,
                        'Symbol': d.symbol,
                        'Action': act,
                        'Mass': '0.0',
                        'Vel_M1': '0.0',
                        'Vel_M5': '0.0',
                        'Vel_M15': '0.0',
                        'Z_Score': '0.0',
                        'Profit': f'{d.profit:.2f}'
                    })
                    added = True
        if added:
            fields = ['Timestamp', 'Symbol', 'Action', 'Mass', 'Vel_M1', 'Vel_M5', 'Vel_M15', 'Z_Score', 'Profit']
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                for r in rows:
                    w.writerow(r)
    except Exception:
        pass

def run_hermes_audit():
    pb_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "playbook_data.csv")
    if not os.path.exists(pb_path):
        pb_path = os.path.join(os.path.dirname(__file__), "playbook_data.csv")
        
    trades = load_playbook_data(pb_path)
    
    if len(trades) == 0:
        print(f"[{time.strftime('%H:%M:%S')}] [HERMES] No trade data exists yet in playbook_data.csv. Waiting for MT5 bot...")
        return len(trades)
        
    final_prompt = build_prompt(trades)
    call_hermes(final_prompt)
    return len(trades)

if __name__ == "__main__":
    import time
    
    # Check for CLI args
    loop_mode = "--loop" in sys.argv or "-l" in sys.argv
    for arg in sys.argv[1:]:
        if arg.startswith("sk-"):
            DEEPSEEK_API_KEY = arg

    if loop_mode:
        print("\n" + "="*60)
        print("  HERMES AI OVERLORD — CONTINUOUS LIVE MONITOR")
        print(f"  Powered by DeepSeek AI (deepseek-chat)")
        print(f"  Monitoring MT5 playbook_data.csv & evolving DNA...")
        print("="*60 + "\n")
        
        last_trade_count = 0
        while True:
            try:
                pb_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "playbook_data.csv")
                if not os.path.exists(pb_path):
                    pb_path = os.path.join(os.path.dirname(__file__), "playbook_data.csv")
                
                current_trades = load_playbook_data(pb_path)
                current_count = len(current_trades)
                
                if current_count != last_trade_count and current_count > 0:
                    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] [HERMES] New trade activity detected ({current_count} total rows). Running Neural Audit...")
                    last_trade_count = run_hermes_audit()
                else:
                    print(f"[{time.strftime('%H:%M:%S')}] [HERMES WATCHDOG] Standing by. {current_count} total trades in database. Waiting for next MT5 trade event...", end="\r", flush=True)
                    
                time.sleep(30)
            except KeyboardInterrupt:
                print("\n[HERMES] Monitor shutting down...")
                break
    else:
        run_hermes_audit()
