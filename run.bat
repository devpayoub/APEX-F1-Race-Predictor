@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

echo.
echo  ==========================================
echo   APEX - F1 Race Predictor
echo  ==========================================

if not exist "%VENV_PY%" goto setup
"%VENV_PY%" -c "import numpy, pandas, sklearn, requests" >nul 2>&1
if errorlevel 1 goto setup
goto run

:setup
echo.
echo  Preparing Python environment - first run only, takes a few minutes.
echo.
if not exist "%VENV_PY%" (
    echo  Creating virtual environment .venv ...
    py -3 -m venv .venv >nul 2>&1
    if not exist "%VENV_PY%" python -m venv .venv >nul 2>&1
)
if not exist "%VENV_PY%" goto nopython

echo  Installing dependencies ...
"%VENV_PY%" -m pip install --upgrade pip --quiet
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto pipfail
echo  Environment ready.
echo.

:run
if /I "%~1"=="--web" goto web
"%VENV_PY%" run.py %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" goto failed
echo  Done. Open dashboard\index.html to view the forecast.
echo.
pause
exit /b 0

:web
echo  Starting the web UI on http://localhost:5000
echo  Press CTRL+C to stop.
echo.
start "" http://localhost:5000
"%VENV_PY%" app.py
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%

:failed
echo  [ERROR] run.py exited with code %RC%.
echo.
pause
exit /b %RC%

:nopython
echo.
echo  [ERROR] Could not create a virtual environment.
echo          Install Python 3.10 or newer from https://www.python.org/downloads/
echo          and tick "Add python.exe to PATH" during installation.
echo.
pause
exit /b 1

:pipfail
echo.
echo  [ERROR] Dependency installation failed.
echo          Check your internet connection and run this file again.
echo.
pause
exit /b 1
