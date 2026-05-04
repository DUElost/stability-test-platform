#!/usr/bin/env pwsh
#
# copy-key.ps1 - 复制 SSH 公钥到 Linux 目标机，实现免密登录
#
# 用法示例:
#   .\copy-key.ps1 172.21.15.101
#   .\copy-key.ps1 172.21.15.101 -User username
#
param(
    [Parameter(Mandatory = $true)]
    [string]$HostName,

    [Parameter(Mandatory = $false)]
    [string]$User = "android",

    [Parameter(Mandatory = $false)]
    [string]$PublicKeyPath = "$env:USERPROFILE\.ssh\id_ed25519.pub",

    [Parameter(Mandatory = $false)]
    [switch]$Force = $false
)

# 彩色输出封装
function Write-Success { Write-Host @args -ForegroundColor Green }
function Write-Error   { Write-Host @args -ForegroundColor Red }
function Write-Warning { Write-Host @args -ForegroundColor Yellow }
function Write-Info    { Write-Host @args -ForegroundColor Cyan }

Write-Info "========================================="
Write-Info "SSH 公钥复制工具"
Write-Info "========================================="
Write-Info ""

# 1) 校验公钥文件是否存在
if (-not (Test-Path $PublicKeyPath)) {
    Write-Error "错误: 公钥文件不存在 $PublicKeyPath"
    Write-Info ""
    Write-Info "请先生成 SSH 密钥对:"
    Write-Info "  ssh-keygen -t ed25519 -C 'your_email@example.com'"
    exit 1
}

# 2) 读取并规范化公钥内容
$publicKey     = (Get-Content $PublicKeyPath -Raw).Trim()
$escapedKey    = $publicKey.Replace("`r", "").Replace("`n", "")
Write-Info "公钥文件: $PublicKeyPath"
Write-Info "公钥内容:"
Write-Host $escapedKey
Write-Info ""

# 3) 组装远端执行命令
#    - 创建 ~/.ssh 并设置目录权限
#    - 写入 authorized_keys (如不存在则创建)
#    - 避免重复写入同一公钥
$remoteCommand = "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && grep -qxF '$escapedKey' ~/.ssh/authorized_keys || echo '$escapedKey' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && echo 'SSH 公钥已添加'"

# 4) 检查本地是否安装 ssh 客户端
$hasSSH = $null -ne (Get-Command ssh -ErrorAction SilentlyContinue)
if (-not $hasSSH) {
    Write-Error "错误: 未找到 ssh 命令"
    Write-Info "请安装 OpenSSH Client:"
    Write-Info "  Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0"
    exit 1
}

# 5) 执行复制
Write-Info "正在连接到 $User@$HostName ..."
Write-Info ""

$sshArgs = @(
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null"
)

if ($Force) {
    $sshArgs += "-o", "BatchMode=yes"
}

$sshArgs += "$User@$HostName", $remoteCommand

& ssh @sshArgs

if ($LASTEXITCODE -eq 0) {
    Write-Info ""
    Write-Success "========================================="
    Write-Success "SSH 公钥复制成功!"
    Write-Success "========================================="
    Write-Info ""
    Write-Info "现在可以免密码登录到 $User@$HostName"
    Write-Info "测试连接:"
    Write-Info "  ssh $User@$HostName"
} else {
    Write-Info ""
    Write-Error "========================================="
    Write-Error "SSH 公钥复制失败!"
    Write-Error "========================================="
    Write-Info ""
    Write-Warning "请检查以下项目:" 
    Write-Info "  1. 目标机地址是否正确"
    Write-Info "  2. 用户名是否正确"
    Write-Info "  3. 网络连接是否正常"
    Write-Info "  4. 目标机 SSH 服务是否运行"
    Write-Info ""
    Write-Info "如需手动复制公钥:"
    Write-Info "  1. 复制上面的公钥内容"
    Write-Info "  2. SSH 登录到目标机"
    Write-Info "  3. 执行: mkdir -p ~/.ssh && chmod 700 ~/.ssh"
    Write-Info "  4. 执行: echo '$escapedKey' >> ~/.ssh/authorized_keys"
    Write-Info "  5. 执行: chmod 600 ~/.ssh/authorized_keys"
}
