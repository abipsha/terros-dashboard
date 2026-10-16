@echo off
cd /d "C:\Users\aashi\Claude\Projects\terros-dashboard"
echo.
echo  Clearing stale git locks...
if exist ".git\index.lock" del /q ".git\index.lock"
if exist ".git\HEAD.lock"  del /q ".git\HEAD.lock"
del /q ".git\*.lock.stale-*" 2>nul
del /q ".git\objects\maintenance.lock.stale-cleared" 2>nul
for /d %%D in (".git\objects\??") do del /q "%%D\tmp_obj_*" 2>nul
echo.
echo  Commits waiting to go up:
git log --oneline origin/main..HEAD
echo.
echo  Pushing to GitHub...
echo  (this script does NOT stage or commit anything - push only)
git push origin main
echo.
echo  Done. Check above for errors.
echo.
pause
