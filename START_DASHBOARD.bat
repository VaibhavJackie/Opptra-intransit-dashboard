@echo off
cd /d "%~dp0"

echo ============================================
echo  In-Transit Dashboard — Opptra
echo ============================================
echo.
echo Your network address (share with team):
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /R /C:"IPv4 Address"') do (
    set IP=%%a
    goto :found
)
:found
set IP=%IP:~1%
echo   http://%IP%:8501
echo.
echo Opening in your browser now...
start "" "http://localhost:8501"
python -m streamlit run app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true
pause
