@echo off
title Vivid Terros Dashboard
echo.
echo  Starting Vivid Terros Dashboard...
echo.
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0server.ps1"
pause
