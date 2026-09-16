"""
Cliente OpenRouter Simples - Sem LiteLLM
Trading Agent com prompt caching otimizado
"""
import os
import json
import httpx
from dotenv import load_dotenv

load_dotenv()

class SimpleOpenRouterClient:
    """
    Cliente simples para OpenRouter com cache de prompt
    """
    
    def __init__(self):
        self.api_key = os.getenv('OPENROUTER_API_KEY')
        if not self.api_key:
            raise ValueError("❌ OPENROUTER_API_KEY não encontrada no arquivo .env")
        
        self.base_url = "https://openrouter.ai/api/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/trading-organizer",
            "X-Title": "Trading Agent Organizer"
        }
        
        # Cache de prompts em memória (simples)
        self._prompt_cache = {}
        
        print("✅ OpenRouter Client inicializado")
        print(f"🔑 API Key: {self.api_key[:10]}...")
    
    def completion(self, model, messages, max_tokens=1000, temperature=0.1, cache_key=None):
        """
        Faz requisição para OpenRouter com cache opcional
        """
        
        # Verificar cache se cache_key fornecido
        if cache_key and cache_key in self._prompt_cache:
            print(f"💾 Usando resposta do cache: {cache_key}")
            return self._prompt_cache[cache_key]
        
        # Preparar dados da requisição
        data = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        
        try:
            with httpx.Client() as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self.headers,
                    json=data,
                    timeout=30.0
                )
                response.raise_for_status()
                
                result = response.json()
                
                # Salvar no cache se cache_key fornecido
                if cache_key:
                    self._prompt_cache[cache_key] = result
                    print(f"💾 Resposta salva no cache: {cache_key}")
                
                return result
                
        except Exception as e:
            return {"error": f"❌ Erro na requisição: {e}"}
    
    def get_cached_keys(self):
        """
        Retorna chaves do cache atual
        """
        return list(self._prompt_cache.keys())

class TradingAgentSimple:
    """
    Agente de Trading usando OpenRouter diretamente
    """
    
    def __init__(self):
        self.client = SimpleOpenRouterClient()
        
        # Prompt base que será usado em cache
        self.system_prompt = """
🤖 PROMPT ESPECIALIZADO: AGENTE ORGANIZADOR DE CÓDIGOS TRADING

Você é um ORGANIZADOR EXPERT em códigos de trading (MQL4/MQL5/Pine Script). 
Sua missão é criar e manter uma estrutura de arquivos LIMPA, LÓGICA e ESCALÁVEL.

## 📋 SUAS RESPONSABILIDADES:

### FOCO PRINCIPAL:
- **FTMO compliance** (máxima prioridade)
- **XAUUSD specialists** (prioridade alta)
- **SMC/Order Blocks** (prioridade alta)  
- **Risk management** (prioridade alta)

### ESTRUTURA OBRIGATÓRIA:
```
PROJETO_TRADING_COMPLETO/
├── 📁 EA_FTMO_XAUUSD_ELITE/
├── 📁 CODIGO_FONTE_LIBRARY/
└── 📄 MASTER_INDEX.md
```

### NOMENCLATURA RIGOROSA:
**PADRÃO:** [TIPO]_[NOME]v[VERSAO][ESPECIFICO].[EXT]

**Exemplos:**
- EA_OrderBlocks_v2.1_XAUUSD_FTMO.mq5
- IND_VolumeFlow_v1.3_SMC_Multi.mq4
- SCR_RiskCalculator_v1.0_FTMO.mq5

### REGRAS FTMO CRÍTICAS:
- Max 5% loss em 1 dia
- Max 10% loss total  
- Max 5% profit por dia
- Risk per trade: 0.5-1%
- No martingale
- No grid sem stop loss
- Mínimo 10 dias trading

Sempre responda de forma estruturada e completa.
"""
    
    def analyze_code(self, code_content, filename=""):
        """
        Analisa código de trading
        """
        cache_key = f"analyze_{hash(code_content[:100])}"
        
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"""
Analise este código de trading:

**ARQUIVO:** {filename}
```
{code_content}
```

**FORNEÇA:**
1. 🔍 **Tipo**: EA/Indicator/Script
2. 📈 **Estratégia**: Identificada
3. 💰 **Mercado**: Compatibilidade  
4. ✅ **FTMO**: Compliance (✅/❌)
5. 📝 **Nome sugerido**: Padrão correto
6. 📁 **Pasta destino**: Categorização
7. 🏷️ **Tags**: Para INDEX
8. ⭐ **Qualidade**: Score 1-5

Responda no formato estruturado.
"""}
        ]
        
        response = self.client.completion(
            model="anthropic/claude-3-5-sonnet",
            messages=messages,
            cache_key=cache_key
        )
        
        if "error" in response:
            return response["error"]
        
        return response["choices"][0]["message"]["content"]
    
    def ftmo_compliance_check(self, ea_code):
        """
        Verificação específica de compliance FTMO
        """
        cache_key = f"ftmo_{hash(ea_code[:100])}"
        
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"""
**ANÁLISE FTMO COMPLIANCE:**

```
{ea_code}
```

**VERIFICAR:**
1. ✅/❌ **Risk Management**: Controle de risco por trade
2. ✅/❌ **Daily Drawdown**: Max 5% loss diário
3. ✅/❌ **Max Drawdown**: Max 10% loss total
4. ✅/❌ **Profit Limits**: Max 5% profit diário
5. ✅/❌ **Anti-Martingale**: Sem multiplicação de lotes
6. ✅/❌ **Stop Loss**: Obrigatório em todas as posições

**RESULTADO FINAL:**
- ✅ **FTMO READY** 
- ❌ **NÃO COMPLIANCE** (com motivos)

Seja rigoroso na análise!
"""}
        ]
        
        response = self.client.completion(
            model="anthropic/claude-3-5-sonnet",
            messages=messages,
            cache_key=cache_key
        )
        
        if "error" in response:
            return response["error"]
        
        return response["choices"][0]["message"]["content"]
    
    def organize_files(self, file_list):
        """
        Organização de múltiplos arquivos
        """
        cache_key = f"organize_{hash(str(file_list))}"
        
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"""
**ORGANIZAÇÃO DE ARQUIVOS:**

**ARQUIVOS PARA ORGANIZAR:**
{chr(10).join([f"- {file}" for file in file_list])}

**EXECUTE:**
1. 🏷️ **Renomear** conforme padrão rigoroso
2. 📁 **Categorizar** por estratégia/tipo
3. 🎯 **Priorizar** FTMO compliance
4. 📋 **Criar entries** para INDEX
5. 🏆 **Ranquear** por qualidade

**FORNEÇA:**
- Estrutura completa de pastas
- Lista renomeada de arquivos  
- INDEX entries prontos
- Prioridades de organização

Seja detalhado e prático!
"""}
        ]
        
        response = self.client.completion(
            model="anthropic/claude-3-5-sonnet",
            messages=messages,
            cache_key=cache_key,
            max_tokens=1500
        )
        
        if "error" in response:
            return response["error"]
        
        return response["choices"][0]["message"]["content"]

def main():
    """
    Exemplo de uso
    """
    try:
        agent = TradingAgentSimple()
        print("\n" + "="*60)
        print("🤖 TRADING AGENT OPENROUTER ATIVO")
        print("="*60)
        
        # Teste básico
        sample_code = """
//+------------------------------------------------------------------+
//| EA Scalper XAUUSD                                               |
//+------------------------------------------------------------------+
extern double LotSize = 0.01;
extern int StopLoss = 50;
extern int TakeProfit = 100;
extern double MaxDailyLoss = 0.05; // 5%

void OnTick() {
    if (AccountEquity() / AccountBalance() < 0.95) return; // Daily DD check
    
    if (OrdersTotal() == 0) {
        OrderSend(Symbol(), OP_BUY, LotSize, Ask, 3, 
                 Ask-StopLoss*Point, Ask+TakeProfit*Point);
    }
}
"""
        
        print("🔍 Analisando código exemplo...")
        result = agent.analyze_code(sample_code, "EA_Scalper_XAUUSD.mq4")
        print("\n" + "="*50)
        print("📋 ANÁLISE DO CÓDIGO:")
        print("="*50)
        print(result)
        
        print("\n" + "="*50)
        print("🛡️ VERIFICAÇÃO FTMO:")
        print("="*50)
        ftmo_result = agent.ftmo_compliance_check(sample_code)
        print(ftmo_result)
        
        print(f"\n💾 Cache keys: {agent.client.get_cached_keys()}")
        
    except Exception as e:
        print(f"❌ Erro: {e}")
        print("\n📝 PARA CONFIGURAR:")
        print("1. Obtenha API key: https://openrouter.ai/keys")
        print("2. Crie .env com: OPENROUTER_API_KEY=sua_chave")
        print("3. Execute: python trading_agent_simple.py")

if __name__ == "__main__":
    main()
