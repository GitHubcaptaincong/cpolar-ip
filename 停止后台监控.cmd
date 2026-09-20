@echo off
setlocal
cd /d "%~dp0"
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
  echo [错误] 未找到项目虚拟环境：.venv\Scripts\python.exe
  pause
  exit /b 1
)

"%VENV_PYTHON%" "%~dp0app.py" --exit
echo 已请求退出 cpolar 地址监控。
pause
