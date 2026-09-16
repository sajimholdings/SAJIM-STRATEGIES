import requests
import json

print("🧪 Testando proxy...")

try:
    # Teste health
    response = requests.get("http://127.0.0.1:4000/health", timeout=10)
    if response.status_code == 200:
        print("✅ Proxy funcionando!")
        data = response.json()
        print(f"📊 Status: {data['status']}")
        print(f"🤖 Modelos: {', '.join(data['models'])}")
        
        # Teste chat
        print("\n🧪 Testando chat completion...")
        chat_data = {
            "model": "deepseek-r1",
            "messages": [{"role": "user", "content": "Olá! Você pode me ajudar com códigos MQL4?"}],
            "max_tokens": 100
        }
        
        chat_response = requests.post(
            "http://127.0.0.1:4000/v1/chat/completions",
            headers={"Content-Type": "application/json"},
            json=chat_data,
            timeout=30
        )
        
        if chat_response.status_code == 200:
            result = chat_response.json()
            content = result['choices'][0]['message']['content']
            print(f"✅ Chat funcionando!")
            print(f"📝 Resposta: {content[:100]}...")
        else:
            print(f"❌ Erro no chat: {chat_response.status_code}")
            print(f"📄 Response: {chat_response.text}")
    else:
        print(f"❌ Health check falhou: {response.status_code}")
        
except Exception as e:
    print(f"❌ Erro: {e}")

print("\n🔌 CONFIGURAÇÃO ROO CODE:")
print("   Base URL: http://127.0.0.1:4000/v1")
print("   Modelo: deepseek-r1")
