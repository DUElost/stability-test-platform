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
set "PROJECT_ROOT=D:\MoveData\Users\Rin\Desktop\Stability-Tools\stability-test-platform"
set "LINUX_PATH=/opt/stability-test-agent"
set "SSH_USER=root"
set "IDENTITY_FILE=%USERPROFILE%\.ssh\id_ed25519"

REM 要同步的文件/目录
set "SYNC_ITEMS=backend\agent backend\requirements.txt"

REM 颜色设置 (Windows 10+)
set "INFO=[92m"    # Green
set "WARN=[93m"    # Yellow
set "ERROR=[91m"   # Red
set "RESET=[0m"

REM 主机列表
set HOST_COUNT=4
set HOST_1=172.21.15.101
set HOST_2=172.21.15.102
set HOST_3=172.21.15.103
set HOST_4=172.21.15.104

REM 解析参数 ================
set "TARGET_HOST=%~1"
set "DO_RESTART=%~2"

if "%TARGET_HOST%"=="" (
    echo %ERROR%错误: 请指定目标主机%RESET%
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
    echo %ERROR%错误: 未找到 scp 命令%RESET%
    echo 请安装 OpenSSH Client:
    echo   Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
    exit /b 1
)

echo %INFO%项目根目录: %PROJECT_ROOT%%RESET%
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

echo %INFO%=========================================%RESET%
echo %INFO%开始同步到: %REMOTE_HOST%%RESET%
echo %INFO%=========================================%RESET%

REM 测试连接
echo %INFO%测试 SSH 连接 ...%RESET%
ssh -o ConnectTimeout=5 -o BatchMode=yes %SCP_ARGS% %REMOTE_HOST% "echo ok" >nul 2>&1
if errorlevel 1 (
    echo %ERROR%SSH 连接失败!%RESET%
    echo %WARN%请检查:%RESET%
    echo   1. 主机地址是否正确: %REMOTE_HOST%
    echo   2. SSH 密钥是否已配置
    echo   3. 网络连接是否正常
    echo.
    exit /b 1
)
echo %INFO%SSH 连接成功!%RESET%
echo.

REM 同步文件
for %%i in (%SYNC_ITEMS%) do (
    set "SOURCE=%PROJECT_ROOT%\%%i"
    set "TARGET=%REMOTE_PATH%"

    if not exist "!SOURCE!" (
        echo %WARN%跳过 - 源文件不存在: %%i%RESET%
    ) else (
        echo %INFO%同步: %%i%RESET%
        scp %SCP_ARGS% "!SOURCE!" "!TARGET!" >nul 2>&1
        if errorlevel 1 (
            echo %ERROR%失败: %%i%RESET%
            exit /b 1
        ) else (
            echo %INFO%  成功%RESET%
        )
    )
)

REM 可选: 重启服务
if /i "%DO_RESTART%"=="restart" (
    echo.
    echo %INFO%重启 Agent 服务 ...%RESET%
    ssh %SCP_ARGS% %REMOTE_HOST% "systemctl restart stability-test-agent && systemctl status stability-test-agent --no-pager"
)

echo.
echo %INFO%=========================================%RESET%
echo %INFO%同步完成!%RESET%
echo %INFO%=========================================%RESET%
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
