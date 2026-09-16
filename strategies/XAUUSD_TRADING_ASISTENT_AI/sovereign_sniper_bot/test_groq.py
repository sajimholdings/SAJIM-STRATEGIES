import os
import requests
import json
from dotenv import load_dotenv

load_dotenv(r"c:\Users\Arnold\Desktop\Tete\XAUUSD_TRADING_ASISTENT_AI\.env")

api_key = os.getenv("GROQ_API_KEY")
url = "https://api.groq.com/openai/v1/chat/completions"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}
payload = {
    "model": "llama-3.3-70b-versatile",
    "messages": [
        {"role": "system", "content": "You are a test."},
        {"role": "user", "content": "Test."}
    ]
}

response = requests.post(url, headers=headers, json=payload)
print(response.status_code)
print(response.text)
