#!/usr/bin/env pwsh
#
# fix-ssh-perms.ps1 - 修复 SSH 目录和配置文件权限
#

Write-Host "修复 SSH 目录权限..." -ForegroundColor Cyan

$sshDir = "$env:USERPROFILE\.ssh"
$configPath = "$sshDir\config"

# 确保 .ssh 目录存在
if (-not (Test-Path $sshDir)) {
    New-Item -Path $sshDir -ItemType Directory -Force | Out-Null
    Write-Host "已创建 .ssh 目录" -ForegroundColor Green
}

# 移除旧的 config 文件（如果有权限问题）
if (Test-Path $configPath) {
    Write-Host "移除旧的 config 文件..." -ForegroundColor Yellow
    Remove-Item $configPath -Force -ErrorAction SilentlyContinue
}

# 创建新的 config 文件
Write-Host "创建新的 config 文件..." -ForegroundColor Cyan
"" | Out-File -FilePath $configPath -Encoding ascii

# 设置目录权限
Write-Host "设置目录权限..." -ForegroundColor Cyan
$acl = Get-Acl $sshDir
$acl.SetAccessRuleProtection($true, $false)
$accessRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $env:USERNAME,
    "FullControl",
    "ContainerInherit,ObjectInherit",
    "None",
    "Allow"
)
$acl.SetAccessRule($accessRule)
Set-Acl $sshDir $acl

# 设置 config 文件权限
Write-Host "设置 config 文件权限..." -ForegroundColor Cyan
$acl = Get-Acl $configPath
$acl.SetAccessRuleProtection($true, $false)
$accessRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $env:USERNAME,
    "FullControl",
    "None",
    "None",
    "Allow"
)
$acl.SetAccessRule($accessRule)
Set-Acl $configPath $acl

Write-Host ""
Write-Host "=========================================" -ForegroundColor Green
Write-Host "SSH 权限修复完成!" -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Green
Write-Host ""
Write-Host "现在可以重新运行:" -ForegroundColor Cyan
Write-Host "  .\copy-key.ps1 172.21.15.1" -ForegroundColor White
