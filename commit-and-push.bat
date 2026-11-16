@echo off
cd /d "C:\Users\aashi\Claude\Projects\terros-dashboard"
echo.
echo  Clearing stale git locks...
if exist ".git\index.lock" del /q ".git\index.lock"
if exist ".git\HEAD.lock"  del /q ".git\HEAD.lock"
del /q ".git\*.lock.stale-*" 2>nul
for /d %%D in (".git\objects\??") do del /q "%%D\tmp_obj_*" 2>nul
echo.
echo  Staging the dashboard files...
git add api/commissions.py api/odoo.py api/server.py api/leaderboard.py leaderboard.html tahiti.html onboarding.html vivid-crm-dashboard.html login.html vivid-terros-dashboard.html icons CLAUDE.md commissions-src/part1.html commissions-src/part2.js commissions.html
echo.
echo  What is about to be committed:
git diff --cached --stat
echo.
git commit -F "commissions-src\COMMIT_MSG.txt"
echo.
echo  Pushing to GitHub...
git push origin main
echo.
echo  Done. Check above for errors.
echo.
pause
