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
python "%~dp0server.py"
pause
