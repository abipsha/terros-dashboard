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
git commit -m "Restyle CRM dashboard: dark theme, 3 tabs, Terros iframe, auto-load fix, Revenue Spread section"
echo.
echo  Pushing to GitHub...
git push origin main
echo.
echo  Done! Check above for any errors.
echo.
pause
