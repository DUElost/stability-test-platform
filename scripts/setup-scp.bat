@echo off
REM setup-scp.bat - SCP 快速配置脚本
REM
REM 此脚本帮助您快速配置 Windows 到 Linux 的 SSH/SCP 连接

setlocal enabledelayedexpansion

set "INFO=[92m"
set "WARN=[93m"
set "ERROR=[91m"
set "CYAN=[96m"
set "RESET=[0m"

echo %CYAN%==================================================%RESET%
echo %CYAN%      SCP 配置向导 - Windows 到 Linux 同步      %RESET%
echo %CYAN%==================================================%RESET%
echo.

REM 步骤 1: 检查 OpenSSH
echo %INFO%步骤 1/5: 检查 OpenSSH Client%RESET%
echo ---------------------------------------------------
where ssh >nul 2>&1
if errorlevel 1 (
    echo %ERROR%未找到 OpenSSH Client%RESET%
    echo.
    echo 正在尝试安装 OpenSSH Client...
    echo 这需要管理员权限
    echo.
    powershell -Command "Start-Process powershell -Verb RunAs -ArgumentList '-Command Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0'" 2>nul
    if errorlevel 1 (
        echo %WARN%无法自动安装，请手动执行以下命令:%RESET%
        echo   Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
        pause
        exit /b 1
    )
    echo %INFO%安装完成，请重新运行此脚本%RESET%
    pause
    exit /b 0
) else (
    echo %INFO%OpenSSH Client 已安装%RESET%
)
echo.

REM 步骤 2: 检查/生成 SSH 密钥
echo %INFO%步骤 2/5: 检查 SSH 密钥%RESET%
echo ---------------------------------------------------
if exist "%USERPROFILE%\.ssh\id_ed25519" (
    echo %INFO%SSH 密钥已存在: %USERPROFILE%\.ssh\id_ed25519%RESET%
    choice /C YN /M "是否重新生成密钥"
    if errorlevel 2 goto skip_keygen
)

echo.
echo %INFO%正在生成 SSH 密钥对...%RESET%
echo 建议使用默认设置，按回车继续
echo.
ssh-keygen -t ed25519 -C "stability-test@%USERNAME%" -f "%USERPROFILE%\.ssh\id_ed25519"
if errorlevel 1 (
    echo %ERROR%密钥生成失败%RESET%
    pause
    exit /b 1
)
echo %INFO%密钥生成完成%RESET%

:skip_keygen
echo.

REM 步骤 3: 显示公钥
echo %INFO%步骤 3/5: 复制公钥到 Linux 主机%RESET%
echo ---------------------------------------------------
echo.
echo %CYAN%你的公钥内容:%RESET%
echo ---------------------------------------------------
type "%USERPROFILE%\.ssh\id_ed25519.pub"
echo ---------------------------------------------------
echo.

REM 步骤 4: 配置 SSH Config
echo %INFO%步骤 4/5: 配置 SSH Config%RESET%
echo ---------------------------------------------------

set "SSH_CONFIG=%USERPROFILE%\.ssh\config"
set "SCRIPT_DIR=%~dp0"

if not exist "%USERPROFILE%\.ssh" mkdir "%USERPROFILE%\.ssh"

if exist "%SSH_CONFIG%" (
    echo %WARN%SSH Config 文件已存在%RESET%
    choice /C YN /M "是否覆盖"
    if errorlevel 2 goto skip_config
)

echo.
echo 正在创建 SSH Config...
echo.
copy "%SCRIPT_DIR%ssh-config-example.txt" "%SSH_CONFIG%" >nul 2>&1

REM 替换用户名
powershell -Command "(Get-Content '%SSH_CONFIG%') -replace 'YourName', '%USERNAME%' | Set-Content '%SSH_CONFIG%'"

echo %INFO%SSH Config 已创建: %SSH_CONFIG%%RESET%
echo %WARN%请根据实际情况修改主机 IP 地址%RESET%

:skip_config
echo.

REM 步骤 5: 复制公钥到 Linux
echo %INFO%步骤 5/5: 复制公钥到 Linux 主机%RESET%
echo ---------------------------------------------------
echo.
echo %CYAN%请选择复制公钥的方式:%RESET%
echo   1. 使用 PowerShell 脚本自动复制
echo   2. 手动复制 (显示详细步骤)
echo   3. 跳过 (稍后手动配置)
echo.
choice /C 123 /N /M "请选择 [1-3]: "

if errorlevel 3 goto finish
if errorlevel 2 goto manual_copy
if errorlevel 1 goto auto_copy

:auto_copy
echo.
set /p TARGET_HOST="请输入目标主机 IP (例如 172.21.15.101): "
set /p TARGET_USER="请输入用户名 (默认: root): "
if "!TARGET_USER!"=="" set "TARGET_USER=root"

echo.
echo %INFO%正在复制公钥到 !TARGET_USER!@!TARGET_HOST! ...%RESET%
echo.
echo %CYAN%请输入 !TARGET_USER!@!TARGET_HOST! 的 SSH 密码:%RESET%
echo.

powershell -ExecutionPolicy Bypass -File "%SCRIPT_DIR%copy-key.ps1" -HostName "!TARGET_HOST!" -User "!TARGET_USER!"
goto finish

:manual_copy
echo.
echo %CYAN%手动复制公钥步骤:%RESET%
echo.
echo 1. 复制上面的公钥内容
echo 2. 使用 SSH 登录到 Linux 主机:
echo    ssh root@^<主机IP^>
echo 3. 执行以下命令:
echo    mkdir -p ~/.ssh ^&^& chmod 700 ~/.ssh
echo    echo '公钥内容' ^>^> ~/.ssh/authorized_keys
echo    chmod 600 ~/.ssh/authorized_keys
echo.
echo 4. 测试连接:
echo    ssh root@^<主机IP^>
echo.

:finish
echo.
echo %CYAN%==================================================%RESET%
echo %INFO%配置完成!%RESET%
echo %CYAN%==================================================%RESET%
echo.
echo %CYAN%后续步骤:%RESET%
echo.
echo 1. 测试 SSH 连接:
echo    ssh root@^<主机IP^>
echo.
echo 2. 使用同步脚本:
echo    cd scripts
echo    .\sync-to-linux.bat ^<主机IP^>
echo.
echo 3. 或使用 VS Code Remote SSH:
echo    - 安装 Remote-SSH 插件
echo    - 连接到配置的主机别名 (agent1, agent2, etc.)
echo    - 直接编辑 /opt/stability-test-agent/ 中的文件
echo.
echo %CYAN%配置文件位置:%RESET%
echo   SSH Config: %SSH_CONFIG%
echo   私钥:       %USERPROFILE%\.ssh\id_ed25519
echo   公钥:       %USERPROFILE%\.ssh\id_ed25519.pub
echo.
pause
