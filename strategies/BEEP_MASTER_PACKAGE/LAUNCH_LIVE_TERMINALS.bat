@echo off
title BEEP Master Package Launcher
echo ======================================================================
echo   BEEP MASTER PACKAGE — LAUNCHING LIVE TERMINALS
echo ======================================================================
echo.
echo Launching Terminal 1: MT5 Vacuum Hunter Engine...
start "BEEP — MT5 Vacuum Hunter" powershell -NoExit -Command "cd '%~dp0'; python -u src/mt5_vacuum_hunter.py"

echo Launching Terminal 2: Hermes AI Overlord (DeepSeek)...
start "BEEP — Hermes AI Overlord" powershell -NoExit -Command "cd '%~dp0'; python -u src/hermes_agent.py --loop"

echo.
echo [OK] Both live terminals are now active and visible on your desktop.
echo You can keep this window or close it.
timeout /t 5
