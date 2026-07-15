@echo off
cd /d "C:\Users\aashi\Claude\Projects\terros-dashboard"
echo.
echo  Removing stale git lock if present...
if exist ".git\index.lock" del ".git\index.lock"
echo.
echo  Clearing unfinished merge state if present...
if exist ".git\MERGE_HEAD" del ".git\MERGE_HEAD"
if exist ".git\MERGE_MSG"  del ".git\MERGE_MSG"
echo.
echo  Staging all files...
git add -A
echo.
echo  Committing...
git commit -m "Store Odoo creds in local credentials.bat (gitignored); safe to push odoo.py"
echo.
echo  Pushing to GitHub...
git push origin main
echo.
echo  Done! Check above for any errors.
echo.
pause
