@echo off
REM ============================================================
REM  MemeBot scheduled harvest wrapper.
REM  Launched by Windows Task Scheduler ("MemeBot-Harvest").
REM  Runs the full pipeline: harvest -> edit -> upload -> cleanup.
REM ============================================================
setlocal
cd /d "%~dp0"

set "LOG=bot.log"
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON=%~dp0.venv\Scripts\python.exe"
) else (
    set "PYTHON=python.exe"
)

REM --- Rotate the log past ~5 MB so it can never fill the disk ---
if exist "%LOG%" (
    for %%A in ("%LOG%") do if %%~zA gtr 5000000 (
        move /y "%LOG%" "bot.log.old" >nul 2>&1
    )
)

echo. >> "%LOG%"
echo ================= %date% %time% ================= >> "%LOG%"

"%PYTHON%" main.py --harvest >> "%LOG%" 2>&1

set "RC=%errorlevel%"
echo ---------------- exit code %RC% ---------------- >> "%LOG%"
endlocal & exit /b %RC%
