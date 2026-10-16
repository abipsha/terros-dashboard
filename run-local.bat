@echo off
cd /d "C:\Users\aashi\Claude\Projects\terros-dashboard\api"
echo.
echo  Loading Odoo credentials...
call credentials.bat >nul 2>&1
@echo off
set PORT=8000
echo.
echo  ============================================================
echo   Starting LOCAL server on http://localhost:8000
echo.
echo   Test these in your browser:
echo     http://localhost:8000/crm                     (CRM dashboard)
echo     http://localhost:8000/api/odoo/onboarding     (restored API)
echo.
echo   Leave this window OPEN. Close it to stop the server.
echo  ============================================================
echo.
python server.py
pause
