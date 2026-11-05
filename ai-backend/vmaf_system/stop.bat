@echo off
setlocal enabledelayedexpansion

REM =====================================================================
REM  VMAF Stopper
REM  Kills whatever is listening on 8000 (backend) and 5173 (frontend).
REM  NOTE: ASCII-only on purpose, see start.bat header.
REM =====================================================================

echo.
echo ================================================
echo   VMAF - Stopping services
echo ================================================
echo.

set FOUND=0

for %%P in (8000 5173) do (
    set "PIDS="
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%P " ^| findstr "LISTENING"') do (
        set "PIDS=!PIDS! %%a"
    )

    if defined PIDS (
        for %%i in (!PIDS!) do (
            echo        Killing PID %%i on port %%P
            taskkill /PID %%i /F >nul 2>nul
            set FOUND=1
        )
    ) else (
        echo        No listener on port %%P
    )
)

echo.
if "!FOUND!"=="1" (
    echo Services stopped.
) else (
    echo No running VMAF services found.
)
echo.

timeout /t 3 /nobreak >nul
endlocal