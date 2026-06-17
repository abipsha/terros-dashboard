@echo off
title Vivid Terros Dashboard
echo.
echo  Vivid Terros Dashboard
echo  ======================
echo.
echo  Starting API server on http://localhost:8000
echo  Dashboard will open automatically in your browser.
echo.
python "%~dp0server.py"
pause
