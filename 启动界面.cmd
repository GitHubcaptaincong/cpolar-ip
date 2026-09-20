@echo off
setlocal
cd /d "%~dp0"

set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
set "VENV_PYTHONW=%~dp0.venv\Scripts\pythonw.exe"

if exist "%VENV_PYTHONW%" (
  start "" "%VENV_PYTHONW%" "%~dp0app.py"
  exit /b 0
)

if exist "%VENV_PYTHON%" (
  "%VENV_PYTHON%" "%~dp0app.py"
  exit /b %errorlevel%
)

echo [错误] 未找到项目虚拟环境：.venv\Scripts\python.exe
echo 请先在项目目录运行：python -m venv .venv
echo 如果 python 命令不可用，请重新安装 Python 3.10+ 并加入 PATH。
pause
exit /b 1
