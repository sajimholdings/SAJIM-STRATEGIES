# Iniciar LiteLLM Proxy para Roo Code
# PowerShell Script

Write-Host "==========================================" -ForegroundColor Green
Write-Host "  INICIANDO LITELLM PROXY PARA ROO CODE" -ForegroundColor Green  
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "🚀 Configurações:" -ForegroundColor Yellow
Write-Host "📡 URL: http://127.0.0.1:4000" -ForegroundColor Cyan
Write-Host "🔑 Key: sk-litellm-proxy-key-12345" -ForegroundColor Cyan
Write-Host "🤖 Modelos: qwen-coder, deepseek-r1" -ForegroundColor Cyan
Write-Host ""
Write-Host "💾 Prompt Caching: ATIVO" -ForegroundColor Green
Write-Host "⚡ Rate Limiting: Configurado" -ForegroundColor Green
Write-Host ""
Write-Host "Para parar: Ctrl+C" -ForegroundColor Yellow
Write-Host "==========================================" -ForegroundColor Green

# Verificar se .env existe
if (-not (Test-Path ".env")) {
    Write-Host "❌ Arquivo .env não encontrado!" -ForegroundColor Red
    Write-Host "📝 Crie o arquivo .env com OPENROUTER_API_KEY" -ForegroundColor Yellow
    Read-Host "Pressione Enter para sair"
    exit 1
}

# Ativar ambiente virtual
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
    Write-Host "✅ Ambiente virtual ativado" -ForegroundColor Green
} else {
    Write-Host "❌ Ambiente virtual não encontrado!" -ForegroundColor Red
    exit 1
}

# Iniciar LiteLLM Proxy
try {
    python -m litellm --config litellm_config.yaml --host 127.0.0.1 --port 4000 --detailed_debug
} catch {
    Write-Host "❌ Erro ao iniciar proxy: $_" -ForegroundColor Red
    Read-Host "Pressione Enter para sair"
}

Write-Host ""
Write-Host "🛑 Proxy finalizado." -ForegroundColor Yellow
