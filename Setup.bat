@echo off
rem One-time setup for Image to 3D. Needs Python 3.12 and about 25 GB of free disk space.
title Image to 3D setup
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating the Python environment...
  py -3.12 -m venv .venv || (echo Python 3.12 is needed: https://www.python.org/downloads/ & pause & exit /b 1)
)
".venv\Scripts\python.exe" app\install.py
pause
