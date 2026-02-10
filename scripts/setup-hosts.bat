@echo off
REM ========================================
REM 工具1: setup-hosts.bat
REM 功能: 配置多个 Linux Host 的 SSH 免密登录
REM 用法:
REM   setup-hosts.bat              # 配置所有 hosts
REM   setup-hosts.bat wsl          # 只配置 WSL
REM   setup-hosts.bat linux        # 只配置 Linux Hosts
REM
REM 配置文件: hosts.conf (添加新 host 在该文件中添加一行即可)
REM ========================================

@chcp 65001 >nul
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"

REM SSH 密钥配置
set "SSH_KEY_TYPE=ed25519"
set "SSH_KEY_PATH=%USERPROFILE%\.ssh\id_ed25519"

REM WSL 配置
set "WSL_ENABLED=1"

REM 解析参数
set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=all"

echo ========================================
echo SSH 免密登录配置工具
echo ========================================
echo.
echo 目标: %TARGET%
echo 密钥类型: %SSH_KEY_TYPE%
echo 密钥路径: %SSH_KEY_PATH%
echo.

REM ========================================
REM 步骤1: 生成 SSH 密钥对（如果不存在）
REM ========================================
echo [步骤1] 检查 SSH 密钥...
echo -----------------------------------------

if exist "%SSH_KEY_PATH%" (
    echo SSH 密钥已存在
) else (
    echo 正在生成 SSH 密钥...
    ssh-keygen -t %SSH_KEY_TYPE% -f "%SSH_KEY_PATH%" -N ""
    if errorlevel 1 (
        echo 错误: 密钥生成失败
        pause
        exit /b 1
    )
    echo 密钥已生成
)
echo.

REM ========================================
REM 步骤2: 显示公钥
REM ========================================
echo [步骤2] SSH 公钥内容
echo -----------------------------------------
echo.
type "%SSH_KEY_PATH%.pub"
echo.
echo ========================================
echo.
echo 提示: 公钥将自动添加到以下 hosts
echo.

REM ========================================
REM 步骤3: 配置 WSL
REM ========================================
if /i "%TARGET%"=="wsl" or /i "%TARGET%"=="all" (
    if "%WSL_ENABLED%"=="1" (
        echo [步骤3] 配置 WSL SSH...
        echo -----------------------------------------

        wsl bash -lc "mkdir -p ~/.ssh && chmod 700 ~/.ssh" 2>nul
        wsl bash -lc "cat >> ~/.ssh/authorized_keys" < "%SSH_KEY_PATH%.pub" 2>nul
        wsl bash -lc "chmod 600 ~/.ssh/authorized_keys" 2>nul

        echo [成功] WSL SSH 配置完成
        echo.
    )
)

REM ========================================
REM 步骤4: 配置 Linux Hosts
REM ========================================
if /i "%TARGET%"=="linux" or /i "%TARGET%"=="all" (
    echo [步骤4] 配置 Linux Hosts SSH...
    echo -----------------------------------------

    REM 从配置文件读取 hosts
    set "HOST_COUNT=0"
    for /f "usebackq tokens=*" %%h in ("%SCRIPT_DIR%hosts.conf") do (
        set "LINE=%%h"
        echo.!LINE! | findstr /B "#" >nul 2>&1
        if errorlevel 1 (
            if not "!LINE!"=="" (
                echo.!LINE! | findstr /B "WSL_AGENT_PATH=" >nul 2>&1
                if errorlevel 1 (
                    call :config_ssh_host %%h
                    set /a HOST_COUNT+=1
                )
            )
        )
    )

    if !HOST_COUNT!==0 (
        echo [提示] 未找到任何 host，请编辑 hosts.conf 添加
    )
    echo.
)

REM ========================================
REM 完成
REM ========================================
echo ========================================
echo 配置完成!
echo ========================================
echo.
echo 下一步:
echo   1. 运行 sync-agent.bat 同步代码
echo   2. 运行 run-cmd.bat 测试连接
echo.
pause
exit /b 0

REM ========================================
REM 子程序: 配置单个 Host 的 SSH
REM ========================================
:config_ssh_host
set "HOST_SPEC=%~1"
for /f "tokens=1,2 delims=:" %%a in ("%HOST_SPEC%") do (
    set "LINUX_USER_HOST=%%a"
    set "LINUX_DEST_PATH=%%b"
)

echo 配置主机: !LINUX_USER_HOST!

REM 使用 ssh-copy-id
ssh-copy-id -o StrictHostKeyChecking=no -i "%SSH_KEY_PATH%.pub" !LINUX_USER_HOST! 2>nul
if errorlevel 1 (
    echo   使用备用方法...
    ssh -o StrictHostKeyChecking=no !LINUX_USER_HOST! "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys" < "%SSH_KEY_PATH%.pub" 2>nul
    if errorlevel 1 (
        echo   [失败] !LINUX_USER_HOST! - 请手动配置
    ) else (
        echo   [成功] !LINUX_USER_HOST!
    )
) else (
    echo   [成功] !LINUX_USER_HOST!
)

REM 测试连接
ssh -o StrictHostKeyChecking=no !LINUX_USER_HOST! "echo '连接成功'" 2>nul
if errorlevel 1 (
    echo   [警告] !LINUX_USER_HOST! 连接测试失败
)
exit /b 0
