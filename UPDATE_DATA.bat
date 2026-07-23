@echo off
cd /d "%~dp0"

echo ============================================
echo  Update Dashboard Data — Opptra
echo ============================================
echo.

REM ── Step 1: Find the latest IT file in Downloads ──
set "LATEST_IT="
for /f "delims=" %%f in ('dir /b /o-d "C:\Users\Vaibhav\Downloads\inventory_dataframe*.csv" 2^>nul') do (
    if not defined LATEST_IT set "LATEST_IT=C:\Users\Vaibhav\Downloads\%%f"
)

REM ── Step 2: Find the latest GRN file in Downloads ──
set "LATEST_GRN="
for /f "delims=" %%f in ('dir /b /o-d "C:\Users\Vaibhav\Downloads\india_grn1*.csv" 2^>nul') do (
    if not defined LATEST_GRN set "LATEST_GRN=C:\Users\Vaibhav\Downloads\%%f"
)

if not defined LATEST_IT (
    echo ERROR: No inventory_dataframe*.csv found in Downloads.
    echo Please make sure the In-Transit file is in your Downloads folder.
    pause & exit /b 1
)

if not defined LATEST_GRN (
    echo ERROR: No india_grn1*.csv found in Downloads.
    echo Please make sure the GRN file is in your Downloads folder.
    pause & exit /b 1
)

echo Found IT file:  %LATEST_IT%
echo Found GRN file: %LATEST_GRN%
echo.

REM ── Step 3: Copy to data/ ──
copy /y "%LATEST_IT%"  "data\latest_it.csv"  >nul
copy /y "%LATEST_GRN%" "data\latest_grn.csv" >nul
echo Copied to data/ folder.

REM ── Step 4: Commit and push ──
git add data\latest_it.csv data\latest_grn.csv app.py .gitignore START_DASHBOARD.bat
git commit -m "Data update %DATE% %TIME:~0,5%"
git push

echo.
echo ============================================
echo  Done! Dashboard will refresh in ~2 minutes
echo  at your Streamlit Cloud URL.
echo ============================================
pause
