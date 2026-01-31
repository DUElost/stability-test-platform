#!/usr/bin/env pwsh
#
# sync-to-linux.ps1 - Windows 到 Linux 代码同步脚本
#
# 用法:
#   .\sync-to-linux.ps1 -HostName 172.21.15.101
#   .\sync-to-linux.ps1 -HostName linux-agent-1  # 使用 SSH Config 配置的别名
#   .\sync-to-linux.ps1 -AllHosts            # 同步到所有主机
#   .\sync-to-linux.ps1 -HostName 172.21.15.101 -Restart  # 同步后重启 Agent
#

param(
    [Parameter(Mandatory=$false, ParameterSetName="SingleHost")]
    [string]$HostName,

    [Parameter(Mandatory=$false, ParameterSetName="AllHosts")]
    [switch]$AllHosts,

    [Parameter(Mandatory=$false)]
    [switch]$Restart = $false,

    [Parameter(Mandatory=$false)]
    [string]$IdentityFile = "$env:USERPROFILE\.ssh\id_ed25519",

    [Parameter(Mandatory=$false)]
    [string]$LinuxPath = "/opt/stability-test-agent",

    [Parameter(Mandatory=$false)]
    [string]$ProjectRoot = "$PSScriptRoot\.."
)

# 颜色输出函数
function Write-ColorOutput {
    param(
        [string]$Message,
        [string]$Color = "White"
    )
    Write-Host $Message -ForegroundColor $Color
}

function Write-Success { Write-ColorOutput @args -Color "Green" }
function Write-Error { Write-ColorOutput @args -Color "Red" }
function Write-Warning { Write-ColorOutput @args -Color "Yellow" }
function Write-Info { Write-ColorOutput @args -Color "Cyan" }

# 主机列表配置
$Script:Hosts = @(
    @{ Name = "linux-agent-1"; IP = "172.21.15.101"; HOST_ID = 1 }
    @{ Name = "linux-agent-2"; IP = "172.21.15.102"; HOST_ID = 2 }
    @{ Name = "linux-agent-3"; IP = "172.21.15.103"; HOST_ID = 3 }
    @{ Name = "linux-agent-4"; IP = "172.21.15.104"; HOST_ID = 4 }
)

# 要同步的文件/目录
$SyncItems = @(
    "backend/agent/",
    "backend/requirements.txt"
)

# AIMONKEY 资源文件目录
$AimonkeyResources = @(
    "../Monkey_test/AIMonkeyTest_2025mtk/aim",
    "../Monkey_test/AIMonkeyTest_2025mtk/aimwd",
    "../Monkey_test/AIMonkeyTest_2025mtk/aim.jar",
    "../Monkey_test/AIMonkeyTest_2025mtk/blacklist.txt"
)

# SCP 参数
$SCPCommonArgs = @(
    "-r",                           # 递归复制
    "-p",                           # 保留文件属性
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=10"
)

if (Test-Path $IdentityFile) {
    $SCPCommonArgs += @("-i", $IdentityFile)
}

# 检查 SCP 是否可用
function Test-SCP {
    try {
        $null = Get-Command scp -ErrorAction Stop
        return $true
    } catch {
        Write-Error "错误: 未找到 scp 命令。请安装 OpenSSH Client:"
        Write-Info "   Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0"
        return $false
    }
}

# 检查 SSH 连接
function Test-SSHConnection {
    param([string]$TargetHost)

    Write-Info "正在测试 SSH 连接到 $TargetHost ..."

    $sshArgs = @(
        "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes"
    )

    if (Test-Path $IdentityFile) {
        $sshArgs += @("-i", $IdentityFile)
    }

    $sshArgs += $TargetHost, "echo ok"

    $result = & ssh @sshArgs 2>&1

    if ($LASTEXITCODE -eq 0 -and $result -match "ok") {
        Write-Success "SSH 连接成功!"
        return $true
    } else {
        Write-Error "SSH 连接失败!"
        Write-Warning "请检查:"
        Write-Info "  1. 主机地址是否正确: $TargetHost"
        Write-Info "  2. SSH 密钥是否已配置"
        Write-Info "  3. 网络连接是否正常"
        return $false
    }
}

# 同步文件到指定主机
function Sync-ToHost {
    param(
        [string]$TargetHost,
        [int]$HostId
    )

    Write-Info "========================================="
    Write-Info "开始同步到: $TargetHost (HOST_ID=$HostId)"
    Write-Info "========================================="

    # 测试连接
    if (-not (Test-SSHConnection -TargetHost $TargetHost)) {
        Write-Error "跳过 $TargetHost - 连接失败"
        return $false
    }

    $success = $true

    foreach ($item in $SyncItems) {
        $sourcePath = Join-Path $ProjectRoot $item
        $targetPath = "$TargetHost`:$LinuxPath/"

        Write-Info "同步: $item"

        if (-not (Test-Path $sourcePath)) {
            Write-Warning "  跳过 - 源文件不存在: $sourcePath"
            continue
        }

        # 执行 SCP
        $scpArgs = $SCPCommonArgs + $sourcePath, $targetPath
        $output = & scp @scpArgs 2>&1

        if ($LASTEXITCODE -eq 0) {
            Write-Success "  成功"
        } else {
            Write-Error "  失败"
            Write-Warning $output
            $success = $false
        }
    }

    # 同步 Monkey 资源文件
    Write-Info "同步 AIMONKEY 资源..."
    $resourceDir = "$TargetHost`:$LinuxPath/resources/aimonkey"

    # 先在远程创建目录
    $sshArgs = @(
        "-o", "BatchMode=yes"
    )
    if (Test-Path $IdentityFile) {
        $sshArgs += @("-i", $IdentityFile)
    }
    $sshArgs += $TargetHost, "mkdir -p $LinuxPath/resources/aimonkey"
    $null = & ssh @sshArgs 2>&1

    foreach ($resource in $AimonkeyResources) {
        if (Test-Path $resource) {
            Write-Info "  同步: $(Split-Path $resource -Leaf)"
            $scpArgs = $SCPCommonArgs + $resource, $resourceDir
            $output = & scp @scpArgs 2>&1

            if ($LASTEXITCODE -eq 0) {
                Write-Success "    成功"
            } else {
                Write-Warning "    失败 - 文件可能不存在"
            }
        } else {
            Write-Warning "  跳过 - 源文件不存在: $resource"
        }
    }

    # 设置资源文件可执行权限
    Write-Info "设置 Monkey 资源权限..."
    $sshArgs = $TargetHost, "chmod +x $LinuxPath/resources/aimonkey/aim $LinuxPath/resources/aimonkey/aimwd 2>/dev/null || true"
    $null = & ssh @sshArgs 2>&1

    # 可选：重启 Agent 服务
    if ($Restart -and $success) {
        Write-Info "重启 Agent 服务 ..."

        $sshArgs = @(
            "-o", "BatchMode=yes"
        )

        if (Test-Path $IdentityFile) {
            $sshArgs += @("-i", $IdentityFile)
        }

        $sshArgs += $TargetHost, "systemctl restart stability-test-agent && systemctl status stability-test-agent --no-pager"

        $output = & ssh @sshArgs 2>&1

        if ($LASTEXITCODE -eq 0) {
            Write-Success "Agent 服务已重启"
        } else {
            Write-Warning "Agent 服务重启失败 (可能未安装为服务)"
        }
    }

    return $success
}

# 主逻辑
function Main {
    # 检查 SCP 可用性
    if (-not (Test-SCP)) {
        exit 1
    }

    # 解析项目根目录
    $ProjectRoot = Resolve-Path $ProjectRoot
    Write-Info "项目根目录: $ProjectRoot"
    Write-Info ""

    # 确定目标主机
    $targets = @()

    if ($AllHosts) {
        $targets = $Hosts
    } elseif ($HostName) {
        # 查找匹配的主机
        $matched = $Hosts | Where-Object {
            $_.Name -eq $HostName -or $_.IP -eq $HostName -or $_.IP.StartsWith($HostName)
        }

        if ($matched) {
            $targets = @($matched)
        } else {
            # 直接使用输入的主机名
            $targets = @(@{ Name = $HostName; IP = $HostName; HOST_ID = 0 })
        }
    } else {
        Write-Error "请指定目标主机 (-HostName 或 -AllHosts)"
        Write-Info ""
        Write-Info "可用主机:"
        foreach ($h in $Hosts) {
            Write-Info "  $($h.Name) ($($h.IP)) - HOST_ID=$($h.HOST_ID)"
        }
        exit 1
    }

    # 同步到每个目标主机
    $results = @()

    foreach ($target in $targets) {
        $targetHost = if ($target.IP -match '\d+\.\d+\.\d+\.\d+') { $target.IP } else { $target.Name }
        $result = Sync-ToHost -TargetHost $targetHost -HostId $target.HOST_ID
        $results += [PSCustomObject]@{
            Host = $targetHost
            Success = $result
        }
        Write-Info ""
    }

    # 输出结果摘要
    Write-Info "========================================="
    Write-Info "同步结果摘要"
    Write-Info "========================================="

    foreach ($r in $results) {
        $status = if ($r.Success) { "成功" } else { "失败" }
        $color = if ($r.Success) { "Green" } else { "Red" }
        Write-ColorOutput "  $($r.Host): $status" -Color $color
    }

    $failedCount = ($results | Where-Object { -not $_.Success }).Count
    if ($failedCount -gt 0) {
        Write-Error ""
        Write-Error "有 $failedCount 个主机同步失败"
        exit 1
    } else {
        Write-Success ""
        Write-Success "所有主机同步完成!"
    }
}

# 运行
Main
