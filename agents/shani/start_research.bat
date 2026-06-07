@echo off
setlocal enabledelayedexpansion

title SHANI Research Stack Launcher
color 0A

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║        SHANI × Chitragupta Research Stack        ║
echo  ║              Fully Local — Mistral 7B            ║
echo  ╚══════════════════════════════════════════════════╝
echo.

:: ─────────────────────────────────────────────────────
:: STEP 1 — Check WSL
:: ─────────────────────────────────────────────────────
echo [1/5] Checking WSL...
wsl --status >nul 2>&1
if errorlevel 1 (
    echo  ERROR: WSL not available.
    echo  Fix: Open PowerShell as Admin and run: wsl --install
    pause
    exit /b 1
)
echo       WSL OK
echo.

:: ─────────────────────────────────────────────────────
:: STEP 2 — Start Ollama if not running
:: ─────────────────────────────────────────────────────
echo [2/5] Checking Ollama...
wsl bash -c "curl -s http://localhost:11434/api/tags > /dev/null 2>&1"
if %errorlevel% == 0 (
    echo       Ollama already running  ✓
    goto :check_mistral
)

echo       Starting Ollama...
where wt >nul 2>&1
if %errorlevel% == 0 (
    start "" wt -w 0 new-tab --title "Ollama" wsl bash /mnt/d/SQL_IMP_AI_Project/start_ollama.sh
) else (
    start "" wsl bash /mnt/d/SQL_IMP_AI_Project/start_ollama.sh
)

set /a attempts=0
:wait_ollama
set /a attempts+=1
if !attempts! gtr 20 (
    echo  WARNING: Ollama not responding after 20s.
    goto :check_mistral
)
wsl bash -c "curl -s http://localhost:11434/api/tags > /dev/null 2>&1"
if %errorlevel% == 0 (
    echo       Ollama ready  ✓
    goto :check_mistral
)
timeout /t 1 /nobreak >nul
goto :wait_ollama

:: ─────────────────────────────────────────────────────
:: STEP 3 — Check Mistral is pulled
:: ─────────────────────────────────────────────────────
:check_mistral
echo.
echo [3/5] Checking Mistral model...
wsl bash -c "ollama list 2>/dev/null | grep -q mistral"
if %errorlevel% == 0 (
    echo       Mistral available  ✓
) else (
    echo       Pulling Mistral ^(first time only — may take a few minutes^)...
    wsl bash -c "ollama pull mistral"
    echo       Done  ✓
)
echo.

:: ─────────────────────────────────────────────────────
:: STEP 4 — Start SHANI API
:: ─────────────────────────────────────────────────────
echo [4/5] Checking SHANI API...
wsl bash -c "ss -tlnp 2>/dev/null | grep -q :8000"
if %errorlevel% == 0 (
    echo       SHANI API already running  ✓
    goto :start_chat
)

echo       Starting SHANI API...
where wt >nul 2>&1
if %errorlevel% == 0 (
    start "" wt -w 0 new-tab --title "SHANI API" wsl bash /mnt/d/SQL_IMP_AI_Project/start_shani.sh
) else (
    start "" wsl bash /mnt/d/SQL_IMP_AI_Project/start_shani.sh
)

set /a attempts=0
:wait_api
set /a attempts+=1
if !attempts! gtr 30 (
    echo  WARNING: SHANI API not responding after 30s.
    goto :start_chat
)
wsl bash -c "curl -s http://localhost:8000/docs > /dev/null 2>&1"
if %errorlevel% == 0 (
    echo       SHANI API ready  ✓
    goto :start_chat
)
timeout /t 1 /nobreak >nul
goto :wait_api

:: ─────────────────────────────────────────────────────
:: STEP 5 — Launch Research Chat
:: ─────────────────────────────────────────────────────
:start_chat
echo.
echo [5/5] Launching Research Chat...
echo.

where wt >nul 2>&1
if %errorlevel% == 0 (
    start "" wt -w 0 new-tab --title "Research Chat" wsl bash /mnt/d/SQL_IMP_AI_Project/start_chat.sh
) else (
    start "" wsl bash /mnt/d/SQL_IMP_AI_Project/start_chat.sh
)

echo  ╔══════════════════════════════════════════════════╗
echo  ║                   Stack Ready                    ║
echo  ║                                                  ║
echo  ║  Ollama        →  http://localhost:11434         ║
echo  ║  SHANI API     →  http://localhost:8000/docs     ║
echo  ║  Chat          →  see new terminal window        ║
echo  ║                                                  ║
echo  ║  Chat commands:                                  ║
echo  ║    tools   — list all 29 tools                   ║
echo  ║    clear   — reset conversation                  ║
echo  ║    exit    — quit                                ║
echo  ╚══════════════════════════════════════════════════╝
echo.
pause
endlocal
