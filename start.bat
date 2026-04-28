@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ----------------------------------------------------------------
REM  CMA Editor launcher
REM  - First run: visible window shows install progress
REM  - Subsequent runs: hidden server, browser opens when ready
REM  - Server output logged to server.log
REM  - Server shuts down when the browser tab is closed
REM ----------------------------------------------------------------

REM ---- Hidden server mode (called internally by VBScript) --------
REM   Uses the venv python directly so PATH activation is not required.
REM   stdout + stderr both go to server.log.
if "%~1"=="__srv__" (
    if not exist ".venv\Scripts\python.exe" (
        echo [ERROR] .venv\Scripts\python.exe missing - venv not bootstrapped > server.log
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 > server.log 2>&1
    exit /b
)

REM ---- Kill any process already using port 8000 ------------------
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr " :8000 "') do (
    taskkill /PID %%a /F >nul 2>&1
)
ping -n 2 127.0.0.1 >nul

REM ---- Pick a Python interpreter ---------------------------------
REM   Prefer the py launcher; fall back to python. Reject the
REM   Microsoft Store WindowsApps stub, which silently fails on
REM   `python -m venv` when launched from Explorer.
set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
) else (
    python --version >nul 2>&1
    if not errorlevel 1 set "PY=python"
)

if not defined PY (
    echo.
    echo  [ERROR] Python is not installed or not in PATH.
    echo.
    echo  Please install Python 3.10 or newer from:
    echo    https://www.python.org/downloads/
    echo.
    echo  IMPORTANT: tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

REM ---- Reject Microsoft Store python stub ------------------------
for /f "delims=" %%P in ('%PY% -c "import sys;print(sys.executable)" 2^>nul') do set "_PYEXE=%%P"
echo %_PYEXE% | findstr /i "\\WindowsApps\\" >nul
if not errorlevel 1 (
    echo.
    echo  [ERROR] The detected Python is the Microsoft Store alias:
    echo    %_PYEXE%
    echo.
    echo  This stub cannot create virtual environments reliably.
    echo  Install a real Python from https://www.python.org/downloads/
    echo  and ensure "Add Python to PATH" is ticked.
    echo.
    pause
    exit /b 1
)

REM ---- Create venv if missing ------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment with %_PYEXE% ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo  [ERROR] Could not create virtual environment.
        pause
        exit /b 1
    )
    if not exist ".venv\Scripts\python.exe" (
        echo  [ERROR] venv creation reported success but .venv\Scripts\python.exe is missing.
        pause
        exit /b 1
    )
)

set "VENV_PY=.venv\Scripts\python.exe"

REM ---- Install / update packages when requirements.txt changes --
REM   Hash the file so updates to requirements.txt trigger a re-install
REM   even on machines that already have .venv\.installed from an older build.
for /f "delims=" %%H in ('powershell -Command "Get-FileHash requirements.txt -Algorithm MD5 | Select-Object -ExpandProperty Hash" 2^>nul') do set "_REQ_HASH=%%H"

set "_OLD_HASH="
if exist ".venv\.installed" for /f "delims=" %%L in (.venv\.installed) do set "_OLD_HASH=%%L"

if not "!_REQ_HASH!"=="!_OLD_HASH!" (
    echo.
    echo  ============================================================
    if not defined _OLD_HASH (
        echo   CMA Editor - First-Time Setup
    ) else (
        echo   CMA Editor - New packages detected, updating...
    )
    echo   Installing packages ^(may take a few minutes^)...
    echo  ============================================================
    echo.
    "%VENV_PY%" -m pip install --upgrade pip -q
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo  [ERROR] Package installation failed.
        pause
        exit /b 1
    )
    if not defined _OLD_HASH (
        echo.
        echo  Installing Chromium for Patchright ^(200 MB download^)...
        "%VENV_PY%" -m patchright install chromium
        if errorlevel 1 (
            echo.
            echo  [ERROR] Chromium installation failed.
            pause
            exit /b 1
        )
    )
    echo !_REQ_HASH! > .venv\.installed
    echo.
    echo  ============================================================
    echo   Setup complete!  Starting CMA Editor...
    echo  ============================================================
    echo.
)

REM ---- Launch server in hidden window ----------------------------
set "_vbs=%TEMP%\cma_%RANDOM%.vbs"
(
    echo Set sh = CreateObject("WScript.Shell"^)
    echo sh.Run "cmd /c ""%~f0"" __srv__", 0, False
) > "%_vbs%"
wscript.exe "%_vbs%"
del "%_vbs%" 2>nul

REM ---- Wait until port 8000 is accepting connections -------------
echo  Starting server...
set /a _tries=0
:wait
ping -n 2 127.0.0.1 >nul
powershell -Command "try{(New-Object Net.Sockets.TcpClient('127.0.0.1',8000)).Close();exit 0}catch{exit 1}" >nul 2>&1
if not errorlevel 1 goto :ready
set /a _tries+=1
if %_tries% lss 20 goto :wait

REM ---- Server never came up - show log ---------------------------
echo.
echo  [ERROR] Server did not start within 40 seconds.
echo  Check server.log in the app folder for details.
echo.
if exist server.log (
    type server.log
) else (
    echo  server.log was not created.
)
echo.
pause
exit /b 1

:ready
start "" http://localhost:8000
exit /b 0
