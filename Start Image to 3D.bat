@echo off
rem Opens the Image to 3D app in your browser. Keep this window open while you use it; close it to quit.
title Image to 3D
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Image to 3D isn't set up yet. Double-click Setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" app\server.py
if errorlevel 1 pause
