@echo off
cd /d "%~dp0"
setlocal

set "TASK_NAME=Opptra InTransit Auto-Update"
set "SCRIPT=%~dp0auto_update.py"
set "PYTHON=pythonw"

echo ============================================
echo  Setup: Opptra InTransit Auto-Update Task
echo ============================================
echo.
echo This will register a Windows Task Scheduler task
echo that runs every hour and auto-pushes new IT/GRN
echo files to GitHub whenever you download them.
echo.
echo Script : %SCRIPT%
echo Runs   : Every hour (silently, no window)
echo Log    : %~dp0data\auto_update.log
echo.
pause

REM Check if pythonw exists
where pythonw >nul 2>&1
if errorlevel 1 (
    echo pythonw not found — trying python instead...
    set "PYTHON=python"
)

REM Delete existing task if present (ignore error if not found)
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

set "WRAPPER=%~dp0RUN_AUTO_UPDATE.bat"

REM Create the task: every hour, run via wrapper bat (ensures correct working dir + PATH)
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "cmd /c \"%WRAPPER%\"" ^
  /sc HOURLY ^
  /mo 1 ^
  /st 09:00 ^
  /ru "%USERNAME%" ^
  /f

if errorlevel 1 (
    echo.
    echo ERROR: Could not create scheduled task.
    echo Try running this .bat as Administrator.
    pause
    exit /b 1
)

echo.
echo ✅ Task registered successfully!
echo.
echo How it works:
echo   1. Download your IT + GRN files as usual to Downloads
echo   2. Within ~1 hour the task auto-detects the new files
echo   3. It slims the IT file, pushes to GitHub
echo   4. Dashboard refreshes in ~2 minutes
echo.
echo To trigger immediately (don't wait for next hour):
echo   schtasks /run /tn "%TASK_NAME%"
echo.
echo To view logs:
echo   %~dp0data\auto_update.log
echo.
echo To disable auto-update:
echo   schtasks /delete /tn "%TASK_NAME%" /f
echo.
pause
