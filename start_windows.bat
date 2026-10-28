@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist .venv\Scripts\python.exe goto dependencies
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
if errorlevel 1 (
 echo 需要 Python 3.12 或更新版本，推荐 Python 3.13。请安装 Python 后重新启动。
 pause
 exit /b 1
)
py -3 -m venv .venv
if errorlevel 1 goto failed
:dependencies
.venv\Scripts\python.exe -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
if errorlevel 1 (
 echo 现有虚拟环境的 Python 版本过旧。请先改名保存 .venv 文件夹，安装 Python 3.12+ 后重新启动。
 goto failed
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
.venv\Scripts\python.exe -X utf8 app.py %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo 启动未完成，请查看上方错误。备份文件和检查点会保留。
pause
exit /b 1
