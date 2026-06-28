@echo off
cd /d "%~dp0"
echo Staging changes...
git add -A
echo Committing...
git commit -m "Auto-update: %DATE% %TIME%"
echo Pushing to GitHub...
git push origin master
echo Done.
pause
