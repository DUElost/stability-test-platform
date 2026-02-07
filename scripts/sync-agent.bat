@echo off
REM ========================================
REM 工具2: sync-agent.bat
REM 功能: 同步 agent 代码到 WSL 和 Linux Hosts
REM 用法:
REM   sync-agent.bat              # 同步到 WSL (默认)
REM   sync-agent.bat wsl          # 同步到 WSL
REM   sync-agent.bat linux        # 同步到 Linux Hosts
REM   sync-agent.bat all          # 同步到所有目标
REM   sync-agent.bat wsl restart  # 同步后重启 Agent
REM
REM 配置文件: hosts.conf (添加新 host 在该文件中添加一行即可)
REM ========================================

@chcp 65001 >nul
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
for %%i in ("%PROJECT_ROOT%") do set "PROJECT_ROOT=%%~fi"

REM SSH 配置
set "SSH_KEY=%USERPROFILE%\.ssh\id_ed25519"
set "SSH_OPTS=-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"
if exist "%SSH_KEY%" set "SSH_OPTS=%SSH_OPTS% -i %SSH_KEY%"

REM WSL 配置 (默认值，可在 hosts.conf 中覆盖)
set "WSL_ENABLED=1"
set "WSL_AGENT_PATH=/opt/stability-test-agent"

REM 解析参数
set "TARGET=%~1"
set "DO_RESTART=%~2"

if "%TARGET%"=="" set "TARGET=wsl"

echo ========================================
echo Agent 代码同步工具
echo ========================================
echo.
echo 项目路径: %PROJECT_ROOT%
echo 目标: %TARGET%
echo.

REM 构建 WSL 路径
for /f "delims=" %%i in ('wsl wslpath "%PROJECT_ROOT%" 2^>nul') do set "WSL_PROJECT_ROOT=%%i"

set /a TOTAL_SUCCESS=0
set /a TOTAL_FAILED=0

REM ========================================
REM 同步到 WSL
REM ========================================
if /i "%TARGET%"=="wsl" or /i "%TARGET%"=="all" (
    echo.
    echo [WSL] 开始同步...
    echo -----------------------------------------

    if "%WSL_ENABLED%"=="1" (
        wsl mkdir -p "%WSL_AGENT_PATH%/agent" 2>nul
        wsl rsync -av --progress "%WSL_PROJECT_ROOT%/backend/agent/" "%WSL_AGENT_PATH%/agent/"

        if errorlevel 1 (
            echo [WSL] 同步失败
            set /a TOTAL_FAILED+=1
        ) else (
            echo [WSL] 同步成功
            set /a TOTAL_SUCCESS+=1

            if /i "%DO_RESTART%"=="restart" (
                echo [WSL] 重启 Agent...
                wsl bash -lc "pkill -f 'stability-test-agent' 2>/dev/null; cd %WSL_AGENT_PATH% ^&^& nohup python3 -m agent.main ^> /tmp/agent.log 2^>^&1"
            )
        )
    )
    echo.
)

REM ========================================
REM 同步到 Linux Hosts
REM ========================================
if /i "%TARGET%"=="linux" or /i "%TARGET%"=="all" (
    echo.
    echo [Linux] 开始同步到远程 Hosts...
    echo -----------------------------------------

    REM 从配置文件读取 hosts
    set "HOST_COUNT=0"
    for /f "usebackq tokens=*" %%h in ("%SCRIPT_DIR%hosts.conf") do (
        set "LINE=%%h"
        REM 跳过注释和空行
        echo.!LINE! | findstr /B "#" >nul 2>&1
        if errorlevel 1 (
            if not "!LINE!"=="" (
                echo.!LINE! | findstr /B "WSL_AGENT_PATH=" >nul 2>&1
                if not errorlevel 1 (
                    for /f "tokens=1,2 delims==" %%a in ("!LINE!") do set "WSL_AGENT_PATH=%%b"
                ) else (
                    call :sync_linux_host %%h
                    set /a HOST_COUNT+=1
                )
            )
        )
    )

    if !HOST_COUNT!==0 (
        echo [Linux] 未找到任何 host，请检查 hosts.conf 文件
    )
    echo.
)

REM ========================================
REM 总结
REM ========================================
echo ========================================
echo 同步完成
echo   成功: %TOTAL_SUCCESS%
echo   失败: %TOTAL_FAILED%
echo ========================================
echo.

pause
exit /b 0

REM ========================================
REM 子程序: 同步到单个 Linux Host
REM ========================================
:sync_linux_host
set "HOST_SPEC=%~1"
for /f "tokens=1,2 delims=:" %%a in ("%HOST_SPEC%") do (
    set "LINUX_USER_HOST=%%a"
    set "LINUX_DEST_PATH=%%b"
)

echo.
echo [Linux] 同步到: %LINUX_USER_HOST%
echo         路径: %LINUX_DEST_PATH%

REM 创建父目录（/opt 需要 sudo，然后创建子目录）
ssh %SSH_OPTS% %LINUX_USER_HOST% "sudo mkdir -p %LINUX_DEST_PATH% && sudo chown $(whoami):$(whoami) %LINUX_DEST_PATH%" 2>nul

REM 使用 rsync 或 scp 同步
scp -r %SSH_OPTS% "%PROJECT_ROOT%\backend\agent" "%LINUX_USER_HOST%:%LINUX_DEST_PATH%/"

if errorlevel 1 (
    echo [Linux] 同步失败: %LINUX_USER_HOST%
    set /a TOTAL_FAILED+=1
) else (
    echo [Linux] 同步成功: %LINUX_USER_HOST%
    set /a TOTAL_SUCCESS+=1

    REM 去除 Windows 换行符
    ssh %SSH_OPTS% %LINUX_USER_HOST% "sed -i 's/\r$//' %LINUX_DEST_PATH%/agent/install_agent.sh" 2>nul

    if /i "%DO_RESTART%"=="restart" (
        echo [Linux] 重启 Agent...
        ssh %SSH_OPTS% %LINUX_USER_HOST% "pkill -f stability-test-agent 2>/dev/null; cd %LINUX_DEST_PATH% ^&^& nohup python3 -m agent.main ^> /tmp/agent.log 2^>^&1" 2>nul
    )
)
exit /b 0
