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
git commit -m "Add CRM dashboard, mobile layout, Render config"
echo.
echo  Pushing to GitHub...
git push --force origin main
echo.
echo  Done! Check above for any errors.
echo.
pause
