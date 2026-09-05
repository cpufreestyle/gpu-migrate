@echo off
rem Start GPU monitor (only if not already running)
cd /d "%~dp0"
if exist "C:\Python314\python.exe" (set "PY=C:\Python314\python.exe") else (set "PY=python")

powershell -NoProfile -Command "if (Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*gpu_monitor.py*' }) { exit 1 }"
if %errorlevel%==1 (
    echo [GPU Monitor] already running, nothing to do.
    ping -n 3 127.0.0.1 >nul
    exit /b 0
)

start "GPU Monitor" "%PY%" gpu_monitor.py
echo [GPU Monitor] started. Threshold/log: see config.json and gpu_monitor.log
ping -n 3 127.0.0.1 >nul
