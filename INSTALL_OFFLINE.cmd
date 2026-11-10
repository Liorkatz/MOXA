@echo off
setlocal
cd /d "%~dp0"
if not exist "main.py" goto missing
if not exist "requirements.txt" goto missing
if not exist "wheels\*.whl" goto missing
if exist ".venv\Scripts\python.exe" goto install
python -c "import sys, sysconfig; sys.exit(0 if sys.version_info[:2] == (3, 12) and sysconfig.get_platform() == 'win-amd64' else 1)" >nul 2>&1
if errorlevel 1 goto launcher
python -m venv .venv
if errorlevel 1 goto failed
goto install
:launcher
py -3.12-64 -m venv .venv
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -c "import sys, sysconfig; sys.exit(0 if sys.version_info[:2] == (3, 12) and sysconfig.get_platform() == 'win-amd64' else 1)"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --no-index --find-links=wheels -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "from PySide6 import QtWidgets"
if errorlevel 1 goto failed
call START.cmd
exit /b %errorlevel%
:missing
echo Put this file next to main.py and requirements.txt. Put all WHL files inside wheels.
pause
exit /b 1
:failed
echo Setup failed. Check the output above and verify Python 3.12 x64.
pause
exit /b 1
