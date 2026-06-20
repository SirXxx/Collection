@echo off
chcp 65001 >nul
python "%~dp0fund_gui.py"
if errorlevel 1 (
    echo.
    echo [错误] 启动失败，请确认已安装 Python 并配置了环境变量。
    pause
)
