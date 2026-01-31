@echo off
REM sync-to-linux.bat - Windows 批处理版本的代码同步脚本
REM
REM 用法:
REM   sync-to-linux.bat 172.21.15.101
REM   sync-to-linux.bat linux-agent-1
REM   sync-to-linux.bat all
REM   sync-to-linux.bat 172.21.15.101 restart

setlocal enabledelayedexpansion

REM 配置区域 ================
REM 自动检测项目根目录（脚本所在目录的上一级）
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%\.."
set "LINUX_PATH=/opt/stability-test-agent"
set "SSH_USER=root"
set "IDENTITY_FILE=%USERPROFILE%\.ssh\id_ed25519"

REM 要同步的文件/目录
set "SYNC_ITEMS=backend\agent backend\requirements.txt"

REM 主机列表
set HOST_COUNT=3
set HOST_1=172.21.15.1
set HOST_2=172.21.15.2
set HOST_3=172.21.15.3

REM 解析参数 ================
set "TARGET_HOST=%~1"
set "DO_RESTART=%~2"

if "%TARGET_HOST%"=="" (
    echo 错误: 请指定目标主机
    echo.
    echo 用法:
    echo   sync-to-linux.bat ^<host^>
    echo   sync-to-linux.bat all
    echo   sync-to-linux.bat ^<host^> restart
    echo.
    echo 可用主机:
    for /l %%i in (1,1,%HOST_COUNT%) do (
        echo   - !HOST_%%i!
    )
    exit /b 1
)

REM 检查 scp 是否可用
where scp >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到 scp 命令
    echo 请安装 OpenSSH Client:
    echo   Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
    exit /b 1
)

echo 项目根目录: %PROJECT_ROOT%
echo.

REM 构建 SCP 参数
set "SCP_ARGS=-r -p -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"
if exist "%IDENTITY_FILE%" (
    set "SCP_ARGS=%SCP_ARGS% -i %IDENTITY_FILE%"
)

REM 同步函数 ================
:sync_host
set "REMOTE_HOST=%~1"
set "REMOTE_PATH=%REMOTE_HOST%:%LINUX_PATH%/"

echo =========================================
echo 开始同步到: %REMOTE_HOST%
echo =========================================

REM 测试连接
echo 测试 SSH 连接 ...
ssh -o ConnectTimeout=5 -o BatchMode=yes %SCP_ARGS% %REMOTE_HOST% "echo ok" >nul 2>&1
if errorlevel 1 (
    echo SSH 连接失败!
    echo 请检查:
    echo   1. 主机地址是否正确: %REMOTE_HOST%
    echo   2. SSH 密钥是否已配置
    echo   3. 网络连接是否正常
    echo.
    exit /b 1
)
echo SSH 连接成功!
echo.

REM 同步文件
for %%i in (%SYNC_ITEMS%) do (
    set "SOURCE=%PROJECT_ROOT%\%%i"
    set "TARGET=%REMOTE_PATH%"

    if not exist "!SOURCE!" (
        echo 跳过 - 源文件不存在: %%i
    ) else (
        echo 同步: %%i
        scp %SCP_ARGS% "!SOURCE!" "!TARGET!" >nul 2>&1
        if errorlevel 1 (
            echo 失败: %%i
            exit /b 1
        ) else (
            echo   成功
        )
    )
)

REM 可选: 重启服务
if /i "%DO_RESTART%"=="restart" (
    echo.
    echo 重启 Agent 服务 ...
    ssh %SCP_ARGS% %REMOTE_HOST% "systemctl restart stability-test-agent && systemctl status stability-test-agent --no-pager"
)

echo.
echo =========================================
echo 同步完成!
echo =========================================
exit /b 0

REM 主逻辑 ================
if /i "%TARGET_HOST%"=="all" (
    for /l %%i in (1,1,%HOST_COUNT%) do (
        call :sync_host !HOST_%%i!
        echo.
    )
) else (
    call :sync_host %TARGET_HOST%
)

endlocal
