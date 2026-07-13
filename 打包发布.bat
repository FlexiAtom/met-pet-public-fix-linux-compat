@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
title MeaPet Release Packager

echo [MeaPet] 正在准备安全分享包...
echo [MeaPet] 默认不会包含 config.json、密钥、截图、日志、缓存或模型权重。
echo.

if exist ".venv\Scripts\python.exe" goto use_venv

where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 goto use_py
)

where python >nul 2>&1
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 goto use_python
)

echo [MeaPet] 打包失败：未找到 Python 3.10 或更高版本。
echo [MeaPet] 可以先运行“启动桌宠.bat”创建 .venv，再重新打包。
set "ERR=1"
goto end

:use_venv
".venv\Scripts\python.exe" scripts\package_release.py %*
set "ERR=%ERRORLEVEL%"
goto end

:use_py
py -3 scripts\package_release.py %*
set "ERR=%ERRORLEVEL%"
goto end

:use_python
python scripts\package_release.py %*
set "ERR=%ERRORLEVEL%"

:end
echo.
if "%ERR%"=="0" (
    echo [MeaPet] 完成。分享 dist 目录中的 ZIP 和对应 .sha256 文件即可。
) else (
    echo [MeaPet] 未生成分享包，请根据上方提示处理。
)
pause
exit /b %ERR%
