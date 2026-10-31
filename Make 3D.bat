@echo off
rem Double-click to pick pictures, or drag pictures onto this file. Models go to the output folder.
cd /d "%~dp0"
".venv\Scripts\python.exe" app\make3d.py %*
echo.
pause
