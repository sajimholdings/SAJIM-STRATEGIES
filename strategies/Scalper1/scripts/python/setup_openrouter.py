"""
Configuração LiteLLM + OpenRouter com Prompt Caching
Agente Organizador Trading - API Setup
"""
import os
from dotenv import load_dotenv
import litellm

# Carregar variáveis de ambiente
load_dotenv()

def setup_openrouter_with_caching():
    """
    Configura LiteLLM para usar OpenRouter com prompt caching
    """
    
    # Configurações OpenRouter
    api_key = os.getenv('OPENROUTER_API_KEY')
    if not api_key:
        print("❌ ERRO: OPENROUTER_API_KEY não encontrada no .env")
        print("📝 Crie um arquivo .env com: OPENROUTER_API_KEY=sua_chave_aqui")
        return False
    
    # Configurar LiteLLM
    litellm.api_key = api_key
    litellm.api_base = "https://openrouter.ai/api/v1"
    
    # Ativar cache de prompt
    litellm.cache = litellm.Cache(type="redis")  # ou "local" para cache local
    
    print("✅ LiteLLM configurado com OpenRouter")
    print(f"🔑 API Key: {api_key[:10]}...")
    print("💾 Prompt caching ativado")
    
    return True

def test_openrouter_connection():
    """
    Testa conexão com OpenRouter
    """
    try:
        response = litellm.completion(
            model="openrouter/anthropic/claude-3-5-sonnet",
            messages=[
                {"role": "user", "content": "Responda apenas 'OK' para testar a conexão"}
            ],
            max_tokens=5
        )
        
        print("✅ Teste de conexão bem-sucedido!")
        print(f"📤 Resposta: {response.choices[0].message.content}")
        return True
        
    except Exception as e:
        print(f"❌ Erro na conexão: {e}")
        return False

def available_models():
    """
    Lista modelos disponíveis no OpenRouter
    """
    models = [
        "openrouter/anthropic/claude-3-5-sonnet",
        "openrouter/anthropic/claude-3-opus",
        "openrouter/openai/gpt-4-turbo",
        "openrouter/openai/gpt-4o",
        "openrouter/meta-llama/llama-3.1-70b-instruct",
        "openrouter/google/gemini-pro",
    ]
    
    print("📋 Modelos recomendados para trading:")
    for model in models:
        print(f"   • {model}")
    
    return models

if __name__ == "__main__":
    print("🤖 Configurando LiteLLM + OpenRouter + Prompt Caching")
    print("=" * 60)
    
    if setup_openrouter_with_caching():
        available_models()
        test_openrouter_connection()
    else:
        print("\n📝 Para configurar:")
        print("1. Crie conta em: https://openrouter.ai/")
        print("2. Obtenha API key em: https://openrouter.ai/keys")
        print("3. Crie arquivo .env com: OPENROUTER_API_KEY=sua_chave")
        print("4. Execute novamente este script")
