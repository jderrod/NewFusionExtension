@echo off
rem NewFusionExtension dashboard + Fusion supervisor -> http://127.0.0.1:8765
cd /d "%~dp0"
start "NFE harness" pyw -3 harness\server.py
timeout /t 2 >nul
start "" http://127.0.0.1:8765/
