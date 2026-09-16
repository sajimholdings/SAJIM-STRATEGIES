import os
import json
import requests

class GroqGatekeeper:
    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY")
        self.model = "llama-3.1-8b-instant" # Switched to 8b to bypass 70b daily token limits
        self.url = "https://api.groq.com/openai/v1/chat/completions"

    def evaluate_setup(self, market_state: dict, risk_state: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        system_prompt = (
            "You are an institutional risk daemon enforcing a 4-phase state machine strategy for XAUUSD.\n"
            "Rules:\n"
            "1. SCANNING -> ARMED: Transition only if ema_slope_degrees > 20.\n"
            "2. ARMED -> WINDOW_OPEN: Require pullback_count between 1 and 3 counter-trend candles.\n"
            "3. If drawdown_percent > 5.0 or consecutive_losses >= 3, enforce immediate HALT.\n"
            "Output strictly valid raw JSON only. Do not output markdown, text, or explanations. Use this exact schema:\n"
            '{"action": "EXECUTE_LONG" | "EXECUTE_SHORT" | "HOLD" | "RESET", "target_phase": "string", "risk_multiplier": 1.0, "reason": "string"}'
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Market Data: {json.dumps(market_state)}\nRisk Data: {json.dumps(risk_state)}"}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0 # Force maximum determinism
        }

        try:
            response = requests.post(self.url, headers=headers, json=payload, timeout=10.0)
            if response.status_code != 200:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                return {"action": "HOLD", "target_phase": "SCANNING", "risk_multiplier": 0.0, "reason": f"Gatekeeper API Error: {error_msg}"}
                
            res_json = response.json()
            if 'choices' not in res_json:
                return {"action": "HOLD", "target_phase": "SCANNING", "risk_multiplier": 0.0, "reason": f"Gatekeeper API Error: Missing choices. Raw: {res_json}"}
                
            return json.loads(res_json['choices'][0]['message']['content'])
        except Exception as e:
            # Absolute Safety Fault-Tolerance: If API fails, return hard safety hold
            return {"action": "HOLD", "target_phase": "SCANNING", "risk_multiplier": 0.0, "reason": f"Gatekeeper API Error: {str(e)}"}
