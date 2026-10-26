@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo Run setup.cmd first. & exit /b 1)
".venv\Scripts\python.exe" scripts\run_tests.py
exit /b %errorlevel%
