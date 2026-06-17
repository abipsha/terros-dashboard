@echo off
title Vivid Terros Dashboard
echo.
echo  Restarting Vivid Terros Dashboard...
echo.
taskkill /F /IM python.exe /T >nul 2>&1
timeout /t 1 /nobreak >nul
echo  Starting API server on http://localhost:8000
echo  Dashboard will open automatically in your browser.
echo.
python "%~dp0server.py"
pause
