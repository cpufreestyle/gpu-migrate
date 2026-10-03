@echo off
rem Stop the running GPU monitor (console python, pythonw tray, or the exe)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe' OR Name='GPUMigrate.exe'\" | Where-Object { $_.CommandLine -like '*gpu_monitor.py*' -or $_.Name -eq 'GPUMigrate.exe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Output ('stopped pid ' + $_.ProcessId) }"
ping -n 3 127.0.0.1 >nul
