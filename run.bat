@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "PORT=5000"

echo.
echo  ==========================================
echo   APEX - F1 Race Predictor
echo  ==========================================

REM ---- dependency bootstrap (first run only) --------------------------
if not exist "%VENV_PY%" goto setup
"%VENV_PY%" -c "import numpy, sklearn, requests, flask" >nul 2>&1
if errorlevel 1 goto setup
goto dispatch

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

REM ---- what to run ----------------------------------------------------
:dispatch
if "%~1"=="" goto web
if /I "%~1"=="--web" goto web
if /I "%~1"=="--local" goto web
if /I "%~1"=="--cli" goto cli
goto cli_args

REM ---- local web app (default: double-click to test) ------------------
:web
echo  Local test mode
echo  Serving on http://localhost:%PORT%   - press CTRL+C to stop.
echo.
set "F1_OPEN_BROWSER=1"
"%VENV_PY%" app.py
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo  [ERROR] Server exited with code %RC%.
echo  Server stopped.
echo.
pause
exit /b %RC%

REM ---- CLI ------------------------------------------------------------
:cli
"%VENV_PY%" run.py
set "RC=%ERRORLEVEL%"
goto cli_done

:cli_args
"%VENV_PY%" run.py %*
set "RC=%ERRORLEVEL%"

:cli_done
echo.
if not "%RC%"=="0" goto failed
echo  Done. Open dashboard\index.html to view the forecast.
echo  Tip: run "run.bat" with no arguments to start the local web app instead.
echo.
pause
exit /b 0

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
