@echo off
title PRAMAAN - Ollama Startup

echo ==========================================
echo        PRAMAAN - Ollama Model Warmup
echo ==========================================
echo.

where ollama >nul 2>&1
if errorlevel 1 (
    echo ERROR: Ollama is not installed or not available in PATH.
    pause
    exit /b 1
)

echo [1/3] Checking Ollama server...

curl -s http://127.0.0.1:11434/api/tags >nul 2>&1

if errorlevel 1 (
    echo Ollama server is not running.
    echo Starting Ollama...
    start "" /B ollama serve
    timeout /t 5 /nobreak >nul
) else (
    echo Ollama server is already running.
)

echo.
echo [2/3] Checking qwen2.5vl:7b...

ollama list | findstr /C:"qwen2.5vl:7b" >nul

if errorlevel 1 (
    echo ERROR: qwen2.5vl:7b is not installed.
    echo.
    echo Run this command first:
    echo ollama pull qwen2.5vl:7b
    pause
    exit /b 1
)

echo Model found.

echo.
echo [3/3] Warming up model...

curl -s http://127.0.0.1:11434/api/generate ^
-H "Content-Type: application/json" ^
-d "{\"model\":\"qwen2.5vl:7b\",\"prompt\":\"READY\",\"stream\":false,\"keep_alive\":\"30m\"}" >nul

echo.
echo ==========================================
echo SUCCESS
echo Ollama is running.
echo qwen2.5vl:7b is loaded and warmed up.
echo Model will stay active for approximately 30 minutes.
echo ==========================================
echo.
pause