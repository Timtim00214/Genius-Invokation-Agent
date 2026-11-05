@echo off
setlocal enabledelayedexpansion

REM =====================================================================
REM  VMAF Launcher
REM  Starts backend (8000) + frontend (5173), then opens the browser.
REM  NOTE: ASCII-only on purpose. Chinese text in .bat breaks cmd parsing.
REM        Chinese instructions live in README_START.md instead.
REM =====================================================================

cd /d "%~dp0"

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo.
echo ================================================
echo   VMAF - Visual Modular Agent Framework
echo ================================================
echo.

echo [1/4] Checking environment...

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found. Install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)

python -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
    echo        Installing backend dependencies...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install backend dependencies.
        pause
        exit /b 1
    )
)

where node >nul 2>nul
if errorlevel 1 (
    echo [ERROR] node not found. Install Node.js 18+ and add to PATH.
    pause
    exit /b 1
)

if not exist "frontend\node_modules" (
    echo        First run: installing frontend dependencies...
    pushd frontend
    call npm install
    popd
    if not exist "frontend\node_modules" (
        echo [ERROR] Failed to install frontend dependencies.
        pause
        exit /b 1
    )
)

echo        OK.
echo.

echo [2/4] Checking ports...

netstat -ano | findstr ":8000 " | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 (
    echo [ERROR] Port 8000 is in use. Run stop.bat first, then retry.
    pause
    exit /b 1
)

netstat -ano | findstr ":5173 " | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 (
    echo [ERROR] Port 5173 is in use. Run stop.bat first, then retry.
    pause
    exit /b 1
)

echo        Ports are free.
echo.

echo [3/4] Starting services...

start "VMAF Backend" cmd /k "cd /d "%~dp0backend" && python -m uvicorn main:app --host 127.0.0.1 --port 8000"

set /a WAIT_COUNT=0
:wait_backend
timeout /t 1 /nobreak >nul
set /a WAIT_COUNT+=1
curl -s -o nul --noproxy "*" http://127.0.0.1:8000/health 2>nul
if not errorlevel 1 goto backend_ready
if !WAIT_COUNT! lss 20 goto wait_backend
echo        [WARN] Backend is slow to start, continuing...
goto backend_done

:backend_ready
echo        Backend ready: http://127.0.0.1:8000
:backend_done

start "VMAF Frontend" cmd /k "cd /d "%~dp0frontend" && npx vite --host 127.0.0.1 --port 5173"

set /a WAIT_COUNT=0
:wait_frontend
timeout /t 1 /nobreak >nul
set /a WAIT_COUNT+=1
curl -s -o nul --noproxy "*" http://127.0.0.1:5173/ 2>nul
if not errorlevel 1 goto frontend_ready
if !WAIT_COUNT! lss 25 goto wait_frontend
echo        [WARN] Frontend is slow to start, open the URL manually.
goto frontend_done

:frontend_ready
echo        Frontend ready: http://127.0.0.1:5173
:frontend_done

echo.
echo [4/4] Opening browser...
start "" http://127.0.0.1:5173

echo.
echo ================================================
echo   Started successfully
echo.
echo   Canvas        : http://127.0.0.1:5173
echo   API docs      : http://127.0.0.1:8000/docs
echo.
echo   Two console windows are now running backend and
echo   frontend. Close them, or run stop.bat, to stop.
echo ================================================
echo.

timeout /t 5 /nobreak >nul
endlocal