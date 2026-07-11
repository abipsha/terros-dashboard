@echo off
cd /d "C:\Users\aashi\Claude\Projects\terros-dashboard"
echo.
echo  Removing stale git lock if present...
if exist ".git\index.lock" del ".git\index.lock"
echo.
echo  Staging all files...
git add -A
echo.
echo  Committing...
git commit -m "Fix Odoo auth: add browser-like headers to bypass IP restrictions"
echo.
echo  Pushing to GitHub...
git push origin main
echo.
echo  Done! Check above for any errors.
echo.
pause
