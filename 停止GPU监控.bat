@echo off
rem Stop the running GPU monitor
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*gpu_monitor.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Output ('stopped pid ' + $_.ProcessId) }"
ping -n 3 127.0.0.1 >nul
