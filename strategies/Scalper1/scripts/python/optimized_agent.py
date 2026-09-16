"""
Trading Agent Otimizado - Funcionando!
Qwen 3 Coder + DeepSeek R1 com rate limiting inteligente
"""
import os
import json
import httpx
from dotenv import load_dotenv
import hashlib
import time

load_dotenv()

class OptimizedTradingAgent:
    """
    Agent otimizado que funciona com os modelos gratuitos
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
            "X-Title": "Optimized Trading Agent"
        }
        
        # Usar apenas o modelo que funciona melhor
        self.primary_model = "deepseek/deepseek-r1-0528:free"  # Funcionou perfeitamente
        self.fallback_model = "qwen/qwen3-coder:free"  # Como backup
        
        # Cache robusto
        self._cache = {}
        
        # Rate limiting
        self.last_request = 0
        self.min_delay = 3.0  # 3 segundos para ser seguro
        
        print("✅ Optimized Trading Agent inicializado")
        print(f"🔑 API Key: {self.api_key[:15]}...")
        print(f"🥇 Modelo Principal: {self.primary_model}")
        print(f"🥈 Modelo Backup: {self.fallback_model}")
        print("💾 Cache ativado para otimização")
    
    def _safe_request(self, model, messages, max_tokens=800):
        """
        Requisição segura com rate limiting
        """
        # Rate limiting
        now = time.time()
        if now - self.last_request < self.min_delay:
            wait_time = self.min_delay - (now - self.last_request)
            print(f"⏳ Aguardando {wait_time:.1f}s...")
            time.sleep(wait_time)
        
        data = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.1
        }
        
        try:
            print(f"🔄 Consultando {model}...")
            
            with httpx.Client() as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self.headers,
                    json=data,
                    timeout=30.0
                )
                
                self.last_request = time.time()
                
                if response.status_code == 429:
                    print("⚠️ Rate limit - aguardando 10s...")
                    time.sleep(10)
                    return {"error": "Rate limit exceeded"}
                
                response.raise_for_status()
                result = response.json()
                print("✅ Resposta recebida")
                return result
                
        except Exception as e:
            return {"error": f"❌ Erro na requisição: {e}"}
    
    def analyze_trading_code(self, code_content, filename=""):
        """
        Análise completa de código trading (usa modelo principal)
        """
        cache_key = f"analyze_{hashlib.md5(code_content.encode()).hexdigest()[:10]}"
        
        if cache_key in self._cache:
            print(f"💾 Cache hit: {filename}")
            return self._cache[cache_key]
        
        messages = [
            {"role": "system", "content": """
Você é um EXPERT em análise de códigos de trading MQL4/MQL5.
Analise o código fornecido e forneça informações ESTRUTURADAS.

SEMPRE responda seguindo este formato:

1. 🔍 TIPO DE CÓDIGO:
   [EA/Indicator/Script/Library]

2. 📈 ESTRATÉGIA IDENTIFICADA:
   [Nome e descrição da estratégia]

3. ⚙️ PRINCIPAIS FUNCIONALIDADES:
   - Lista das funcionalidades principais
   - Parâmetros configuráveis
   - Lógica de entrada/saída

4. 🛡️ FTMO COMPLIANCE:
   ✅ APROVADO - Se atende regras FTMO
   ⚠️ RISCO - Se tem pontos de atenção
   ❌ REJEITADO - Se não atende regras

   REGRAS FTMO:
   - Max 5% perda diária
   - Max 10% perda total
   - Stop Loss obrigatório
   - Sem martingale
   - Risk management

5. 📊 QUALIDADE DO CÓDIGO:
   Score 1-10 com justificativa

6. 🏷️ NOME SUGERIDO:
   [TIPO]_[ESTRATEGIA]_v[VER]_[MERCADO].[EXT]

7. 📁 CATEGORIZAÇÃO:
   Pasta de destino sugerida

8. ⚠️ OBSERVAÇÕES:
   Pontos de atenção ou melhorias

Seja TÉCNICO, PRECISO e ESTRUTURADO.
"""},
            {"role": "user", "content": f"""
ANÁLISE DE CÓDIGO TRADING:

ARQUIVO: {filename}

CÓDIGO:
```
{code_content}
```

Forneça análise completa seguindo o formato estruturado.
"""}
        ]
        
        response = self._safe_request(self.primary_model, messages, max_tokens=1200)
        
        if "error" in response:
            return response["error"]
        
        content = response["choices"][0]["message"]["content"]
        self._cache[cache_key] = content
        return content
    
    def organize_file_structure(self, file_list):
        """
        Organização de estrutura de arquivos (usa modelo principal)
        """
        cache_key = f"organize_{hashlib.md5(str(file_list).encode()).hexdigest()[:10]}"
        
        if cache_key in self._cache:
            print("💾 Cache hit: organização")
            return self._cache[cache_key]
        
        messages = [
            {"role": "system", "content": """
Você é um ORGANIZADOR MASTER de códigos de trading.
Crie uma estrutura PROFISSIONAL e FUNCIONAL.

ESTRUTURA PADRÃO:
```
📁 TRADING_PROJECT_ELITE/
├── 📁 01_EA_FTMO_READY/
│   ├── XAUUSD_Scalpers/
│   ├── EURUSD_Systems/
│   └── Multi_Currency/
├── 📁 02_EA_HIGH_RISK/
│   ├── Martingale_Systems/
│   └── Grid_Systems/
├── 📁 03_INDICATORS/
│   ├── SMC_OrderBlocks/
│   ├── Volume_Analysis/
│   └── Trend_Systems/
├── 📁 04_SCRIPTS/
│   ├── Risk_Management/
│   └── Utilities/
└── 📁 05_DOCUMENTATION/
    ├── INDEX_MASTER.md
    ├── FTMO_READY.md
    └── INSTALLATION_GUIDE.md
```

NOMENCLATURA RIGOROSA:
[TIPO]_[ESTRATEGIA]_v[VERSAO]_[MERCADO]_[ESPECIAL].[EXT]

Exemplos:
- EA_OrderBlocks_v2.1_XAUUSD_FTMO.mq5
- IND_VolumeFlow_v1.3_Multi_SMC.mq4
- SCR_RiskCalc_v1.0_Universal_Tool.mq5

SEMPRE responda com:
1. 🏗️ ESTRUTURA DE PASTAS completa
2. 🏷️ RENOMEAÇÃO de todos os arquivos
3. 📋 INDEX entries formatados
4. 🎯 PRIORIZAÇÃO (FTMO primeiro)
5. 📊 RANKING por qualidade
"""},
            {"role": "user", "content": f"""
ORGANIZAÇÃO DE ARQUIVOS:

LISTA DE ARQUIVOS:
{chr(10).join([f"- {arquivo}" for arquivo in file_list])}

Organize seguindo a estrutura profissional e nomenclatura rigorosa.
"""}
        ]
        
        response = self._safe_request(self.primary_model, messages, max_tokens=1500)
        
        if "error" in response:
            return response["error"]
        
        content = response["choices"][0]["message"]["content"]
        self._cache[cache_key] = content
        return content
    
    def quick_test(self):
        """
        Teste rápido do sistema
        """
        test_code = """
//+------------------------------------------------------------------+
//| EA Scalper FTMO                                                 |
//+------------------------------------------------------------------+
extern double LotSize = 0.01;
extern int StopLoss = 50;
extern int TakeProfit = 100;
extern double MaxDailyLoss = 0.05;

bool DailyLossCheck() {
    return (AccountEquity() / AccountBalance()) > 0.95;
}

void OnTick() {
    if (!DailyLossCheck()) return;
    
    if (OrdersTotal() == 0) {
        OrderSend(Symbol(), OP_BUY, LotSize, Ask, 3, 
                 Ask-StopLoss*Point, Ask+TakeProfit*Point);
    }
}
"""
        
        print("🧪 TESTE RÁPIDO DO SISTEMA:")
        print("="*50)
        
        result = self.analyze_trading_code(test_code, "EA_Scalper_FTMO_Test.mq4")
        print(result)
        
        return result

def main():
    """
    Teste principal do sistema otimizado
    """
    try:
        agent = OptimizedTradingAgent()
        print("\n" + "="*60)
        print("🎯 SISTEMA OTIMIZADO - TESTE FUNCIONAL")
        print("="*60)
        
        # Teste básico
        result = agent.quick_test()
        
        if "error" not in result:
            print("\n✅ SISTEMA FUNCIONANDO PERFEITAMENTE!")
            print(f"💾 Cache entries: {len(agent._cache)}")
            
            # Teste organização
            print("\n📁 TESTE ORGANIZAÇÃO DE ARQUIVOS:")
            print("-" * 40)
            files_test = [
                "scalper_xauusd.mq4",
                "trend_system.mq5",
                "volume_indicator.mq4"
            ]
            
            org_result = agent.organize_file_structure(files_test)
            print(org_result[:300] + "..." if len(org_result) > 300 else org_result)
            
        else:
            print(f"❌ Erro no teste: {result}")
        
        print(f"\n📊 Cache total: {len(agent._cache)} entries")
        print("🎉 TESTE CONCLUÍDO!")
        
    except Exception as e:
        print(f"❌ Erro: {e}")

if __name__ == "__main__":
    main()
