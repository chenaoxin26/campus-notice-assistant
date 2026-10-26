@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  where python >nul 2>nul
  if errorlevel 1 (echo Python 3.11+ is required. See README.md. & exit /b 1)
  python -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
  if errorlevel 1 exit /b 1
  python -m venv .venv
) else (
  py -3 -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
  if errorlevel 1 exit /b 1
  py -3 -m venv .venv
)
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
call build.cmd
exit /b %errorlevel%
