@echo off
cd /d "%~dp0"

echo ============================================
echo  Slim IT file for Streamlit Upload
echo ============================================
echo.

REM Find latest IT file in Downloads
set "LATEST_IT="
for /f "delims=" %%f in ('dir /b /o-d "C:\Users\Vaibhav\Downloads\inventory_dataframe*.csv" 2^>nul') do (
    if not defined LATEST_IT set "LATEST_IT=C:\Users\Vaibhav\Downloads\%%f"
)

if not defined LATEST_IT (
    echo ERROR: No inventory_dataframe*.csv found in Downloads.
    pause & exit /b 1
)

echo Found: %LATEST_IT%
echo Filtering to in-transit rows only...

python -c "
import pandas as pd, sys
df = pd.read_csv(r'%LATEST_IT%', low_memory=False)
df.columns = df.columns.str.strip()
col = next((c for c in df.columns if c.lower() == 'intransit_quantity'), None)
if not col:
    print('ERROR: intransit_quantity column not found')
    sys.exit(1)
df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
before = len(df)
df = df[df[col] > 0]
out = r'C:\Users\Vaibhav\Downloads\it_slim_upload.csv'
df.to_csv(out, index=False)
mb = __import__('os').path.getsize(out) / 1e6
print(f'Done: {before:,} rows -> {len(df):,} rows ({mb:.1f} MB)')
print(f'Saved to: {out}')
"

echo.
echo Upload  C:\Users\Vaibhav\Downloads\it_slim_upload.csv  in the dashboard sidebar.
echo.
pause
