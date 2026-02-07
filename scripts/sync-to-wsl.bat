@echo off
REM sync-to-wsl.bat - 将 Agent 代码同步到 WSL (使用 rsync)
REM 用法:
REM   sync-to-wsl.bat           # 同步到 WSL
REM   sync-to-wsl.bat restart   # 同步后重启 Agent

@chcp 65001 >nul
setlocal enabledelayedexpansion

REM 配置区域 ================
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
for %%i in ("%PROJECT_ROOT%") do set "PROJECT_ROOT=%%~fi"

set "DO_RESTART=%~1"

echo =========================================
echo 同步 Agent 代码到 WSL
echo =========================================
echo.

echo 项目路径: %PROJECT_ROOT%
echo.

REM 使用 wsl 命令直接执行 rsync
echo 开始同步文件...
echo.

REM 构建 WSL 路径
for /f "delims=" %%i in ('wsl wslpath "%PROJECT_ROOT%"') do set "WSL_PROJECT_ROOT=%%i"

REM 执行 rsync 同步
wsl rsync -av --progress "%WSL_PROJECT_ROOT%/backend/agent/" "/opt/stability-test-agent/agent/"

if errorlevel 1 (
    echo 同步失败，请检查 rsync 是否已安装
    echo 在 WSL 中运行: sudo apt install rsync
) else (
    echo.
    echo =========================================
    echo 同步完成!
    echo =========================================
)

echo.

REM 可选：重启 Agent
if /i "%DO_RESTART%"=="restart" (
    echo 重启 Agent 服务...
    wsl bash -lc "pkill -f 'stability-test-agent' 2>/dev/null; cd /opt/stability-test-agent ^&^& nohup python3 -m agent.main ^> /tmp/agent.log 2^>^&1 ^& echo Agent 已启动"
    echo.
)

pause
exit /b 0
