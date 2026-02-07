@echo off
REM ========================================
REM Tool 3: run-cmd.bat
REM Function: Execute commands on multiple Linux Hosts
REM Usage:
REM   run-cmd.bat "command"              # Execute on all hosts
REM   run-cmd.bat wsl "command"          # Execute on WSL only
REM   run-cmd.bat linux "command"        # Execute on Linux Hosts only
REM   run-cmd.bat all "command"          # Execute on all hosts
REM
REM Config file: hosts.conf (Add new host by adding a line)
REM ========================================

chcp 65001 >nul
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"

REM SSH Config
set "SSH_KEY=%USERPROFILE%\.ssh\id_ed25519"
set "SSH_OPTS=-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"
if exist "%SSH_KEY%" set "SSH_OPTS=%SSH_OPTS% -i %SSH_KEY%"

REM WSL Config
set "WSL_ENABLED=1"
set "WSL_AGENT_PATH=/opt/stability-test-agent"

REM Parse arguments
set "TARGET=%~1"
set "COMMAND=%~2"

if "%COMMAND%"=="" (
    set "COMMAND=%TARGET%"
    set "TARGET=all"
)

if "%COMMAND%"=="" (
    echo Usage: run-cmd.bat [wsl^|linux^|all] "command"
    echo.
    echo Examples:
    echo   run-cmd.bat "systemctl status stability-test-agent"
    echo   run-cmd.bat linux "tail -100 /tmp/agent.log"
    pause
    exit /b 0
)

echo ========================================
echo Remote Command Execution Tool
echo ========================================
echo.
echo Target: %TARGET%
echo Command: %COMMAND%
echo.

set /a TOTAL_SUCCESS=0
set /a TOTAL_FAILED=0

REM ========================================
REM Execute on WSL
REM ========================================
if /i "%TARGET%"=="wsl" or /i "%TARGET%"=="all" (
    if "%WSL_ENABLED%"=="1" (
        echo.
        echo [WSL] Executing command...
        echo -----------------------------------------
        wsl bash -lc "cd %WSL_AGENT_PATH% 2>/dev/null; %COMMAND%"
        if errorlevel 1 (
            echo [WSL] Command failed
            set /a TOTAL_FAILED+=1
        ) else (
            echo [WSL] Command completed
            set /a TOTAL_SUCCESS+=1
        )
        echo.
    )
)

REM ========================================
REM Execute on Linux Hosts
REM ========================================
if /i "%TARGET%"=="linux" or /i "%TARGET%"=="all" (
    echo.
    echo [Linux] Executing command...
    echo -----------------------------------------

    REM Read hosts from config file
    set "HOST_COUNT=0"
    for /f "usebackq tokens=*" %%h in ("%SCRIPT_DIR%hosts.conf") do (
        set "LINE=%%h"
        echo.!LINE! | findstr /B "#" >nul 2>&1
        if errorlevel 1 (
            if not "!LINE!"=="" (
                echo.!LINE! | findstr /B "WSL_AGENT_PATH=" >nul 2>&1
                if errorlevel 1 (
                    call :run_cmd_on_host %%h
                    set /a HOST_COUNT+=1
                )
            )
        )
    )

    if !HOST_COUNT!==0 (
        echo [Linux] No hosts found, please check hosts.conf
    )
    echo.
)

REM ========================================
REM Summary
REM ========================================
echo ========================================
echo Execution completed
echo   Success: %TOTAL_SUCCESS%
echo   Failed: %TOTAL_FAILED%
echo ========================================
echo.

pause
exit /b 0

REM ========================================
REM Subroutine: Execute command on single host
REM ========================================
:run_cmd_on_host
set "HOST_SPEC=%~1"
for /f "tokens=1,2 delims=:" %%a in ("%HOST_SPEC%") do (
    set "LINUX_USER_HOST=%%a"
    set "LINUX_DEST_PATH=%%b"
)

echo.
echo [Linux] Host: %LINUX_USER_HOST%
echo -----------------------------------------

REM Use ssh -t for interactive commands (like systemctl status which uses pager)
ssh -t %SSH_OPTS% %LINUX_USER_HOST% "cd %LINUX_DEST_PATH% 2>/dev/null; %COMMAND%"

if errorlevel 1 (
    echo [Linux] Command failed
    set /a TOTAL_FAILED+=1
) else (
    echo [Linux] Command completed
    set /a TOTAL_SUCCESS+=1
)
exit /b 0
