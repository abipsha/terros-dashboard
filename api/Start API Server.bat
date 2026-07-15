@echo off
title Vivid Dashboard Server
echo.
echo  Vivid Dashboard Server
echo  ======================
echo.
echo  Terros Dashboard  →  http://localhost:8000/
echo  CRM Dashboard     →  http://localhost:8000/crm
echo.
echo  Press Ctrl+C to stop.
echo.
if exist "%~dp0credentials.bat" call "%~dp0credentials.bat"
python "%~dp0server.py"
pause
