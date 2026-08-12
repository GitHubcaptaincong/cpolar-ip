@echo off
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if errorlevel 1 (
  start "" /min python.exe app.py --headless
) else (
  start "" pythonw.exe app.py --headless
)
