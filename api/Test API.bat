@echo off
title Terros API Test
echo.
echo  Running API test for last week (June 9-15, 2026)...
echo.
python "%~dp0test_api.py" 2026-06-09 2026-06-15
echo.
pause
