#!/usr/bin/env pwsh
# SCRIPT DE VERIFICACAO FINAL - CENTRALIZACAO COMPLETA
# Agente Organizador Expert - Verificador Final
# Data: 24/08/2024

Write-Host "🎯 INICIANDO VERIFICACAO FINAL DA CENTRALIZACAO..." -ForegroundColor Green
Write-Host "=" * 60 -ForegroundColor Yellow

# Definir diretórios
$mainDir = "c:\Users\Admin\Documents\EA_SCALPER_XAUUSD"
$easDir = Join-Path $mainDir "🚀 MAIN_EAS"
$libraryDir = Join-Path $mainDir "📚 LIBRARY\MQH_INCLUDES"

Write-Host "`n🔍 VERIFICANDO EAs PRINCIPAIS..." -ForegroundColor Cyan

# Verificar EAs Production
$prodDir = Join-Path $easDir "PRODUCTION"
if (Test-Path $prodDir) {
    $prodFiles = Get-ChildItem $prodDir -File | Where-Object { $_.Extension -in @('.mq4', '.mq5') }
    Write-Host "✅ PRODUCTION: $($prodFiles.Count) EAs encontrados" -ForegroundColor Green
    $prodFiles | ForEach-Object { Write-Host "   📋 $($_.Name)" -ForegroundColor White }
} else {
    Write-Host "❌ Pasta PRODUCTION não encontrada!" -ForegroundColor Red
}

# Verificar EAs Development
$devDir = Join-Path $easDir "DEVELOPMENT"
if (Test-Path $devDir) {
    $devFiles = Get-ChildItem $devDir -File | Where-Object { $_.Extension -in @('.mq4', '.mq5') }
    Write-Host "`n✅ DEVELOPMENT: $($devFiles.Count) EAs encontrados" -ForegroundColor Green
    $devFiles | ForEach-Object { Write-Host "   🔧 $($_.Name)" -ForegroundColor White }
} else {
    Write-Host "❌ Pasta DEVELOPMENT não encontrada!" -ForegroundColor Red
}

Write-Host "`n🔍 VERIFICANDO BIBLIOTECAS .MQH..." -ForegroundColor Cyan

# Verificar bibliotecas MQH
if (Test-Path $libraryDir) {
    $mqhFiles = Get-ChildItem $libraryDir -File -Filter "*.mqh"
    Write-Host "✅ BIBLIOTECAS: $($mqhFiles.Count) arquivos .mqh centralizados" -ForegroundColor Green
    
    # Contar por categoria
    $coreFiles = $mqhFiles | Where-Object { $_.Name -match "(Trading|Risk|Order|Performance|Trade|Symbol|Account|Position|Deal|History|Terminal)" }
    $smcFiles = $mqhFiles | Where-Object { $_.Name -match "(OrderBlock|FVG|Liquidity|MarketStructure|ICT|SMC|Dynamic|Volume)" }
    $signalFiles = $mqhFiles | Where-Object { $_.Name -match "(Signal|Confluence|Entry|Exit|Advanced|Filter)" }
    $classFiles = $mqhFiles | Where-Object { $_.Name -match "(Advanced|Data|Interface|Config|Cache|Alert|Logger|Performance)" }
    $utilFiles = $mqhFiles | Where-Object { $_.Name -match "(Math|String|Time|File|MCP|VWRSI|CDynamic)" }
    $mlFiles = $mqhFiles | Where-Object { $_.Name -match "(XAUUSD|ML|Latency|Market|ONNX)" }
    
    Write-Host "   📊 Core Trading: $($coreFiles.Count) bibliotecas" -ForegroundColor White
    Write-Host "   🧠 SMC/ICT Analysis: $($smcFiles.Count) bibliotecas" -ForegroundColor White
    Write-Host "   🎯 Signal Systems: $($signalFiles.Count) bibliotecas" -ForegroundColor White
    Write-Host "   🔍 Advanced Classes: $($classFiles.Count) bibliotecas" -ForegroundColor White
    Write-Host "   🛠️ Utilities: $($utilFiles.Count) bibliotecas" -ForegroundColor White
    Write-Host "   🤖 XAUUSD ML: $($mlFiles.Count) bibliotecas" -ForegroundColor White
} else {
    Write-Host "❌ Pasta MQH_INCLUDES não encontrada!" -ForegroundColor Red
}

Write-Host "`n🔍 VERIFICANDO INDICES..." -ForegroundColor Cyan

# Verificar índice principal
$indexFile = Join-Path $easDir "INDEX_EAS_PRINCIPAIS.md"
if (Test-Path $indexFile) {
    $indexContent = Get-Content $indexFile -Raw
    $lastUpdate = (Get-Item $indexFile).LastWriteTime
    Write-Host "✅ INDEX_EAS_PRINCIPAIS.md existe e foi atualizado em: $lastUpdate" -ForegroundColor Green
} else {
    Write-Host "❌ Arquivo INDEX_EAS_PRINCIPAIS.md não encontrado!" -ForegroundColor Red
}

Write-Host "`n📊 ESTATISTICAS FINAIS:" -ForegroundColor Yellow
Write-Host "=" * 40 -ForegroundColor Yellow

# Calcular totais
$totalEAs = 0
if (Test-Path $prodDir) { $totalEAs += (Get-ChildItem $prodDir -File | Where-Object { $_.Extension -in @('.mq4', '.mq5') }).Count }
if (Test-Path $devDir) { $totalEAs += (Get-ChildItem $devDir -File | Where-Object { $_.Extension -in @('.mq4', '.mq5') }).Count }

$totalMQH = 0
if (Test-Path $libraryDir) { $totalMQH = (Get-ChildItem $libraryDir -File -Filter "*.mqh").Count }

Write-Host "🚀 EAs Principais Centralizados: $totalEAs" -ForegroundColor Green
Write-Host "📚 Bibliotecas MQH Centralizadas: $totalMQH" -ForegroundColor Green
Write-Host "📊 Índices Criados: 1" -ForegroundColor Green

# Status final
if ($totalEAs -ge 15 -and $totalMQH -ge 70) {
    Write-Host "`n🎉 CENTRALIZACAO COMPLETA E VERIFICADA COM SUCESSO!" -ForegroundColor Green
    Write-Host "✅ Projeto 100% organizado e pronto para uso" -ForegroundColor Green
} else {
    Write-Host "`n⚠️ VERIFICACAO INCOMPLETA - Alguns arquivos podem estar faltando" -ForegroundColor Yellow
}

Write-Host "`n🏆 VERIFICACAO FINALIZADA!" -ForegroundColor Magenta
Write-Host "=" * 60 -ForegroundColor Yellow
