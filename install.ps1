# PowerShell安装脚本
Write-Host "开始安装 Veins IOV Simulator 后端..." -ForegroundColor Cyan

# 检查是否以管理员权限运行
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "请以管理员身份运行此脚本!" -ForegroundColor Red
    Pause
    exit
}

# 检查winget是否可用
$wingetInstalled = Get-Command winget -ErrorAction SilentlyContinue
if (-not $wingetInstalled) {
    Write-Host "错误：未找到winget命令。请确保您使用的是Windows 10/11并已安装App Installer。" -ForegroundColor Red
    Write-Host "您可以从Microsoft Store安装App Installer。" -ForegroundColor Yellow
    Pause
    exit
}

# 检查Docker Desktop是否已安装
$dockerInstalled = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerInstalled) {
    Write-Host "正在使用winget安装Docker Desktop..." -ForegroundColor Yellow
    winget install -e --id Docker.DockerDesktop

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Docker Desktop安装失败，请手动安装。" -ForegroundColor Red
        Pause
        exit
    }

    Write-Host "Docker Desktop已安装，请重启计算机并在Docker Desktop启动后再运行此脚本。" -ForegroundColor Green
    Pause
    exit
} else {
    Write-Host "检测到Docker已安装，继续构建镜像..." -ForegroundColor Green
}

# 等待Docker服务启动
Write-Host "等待Docker服务启动..." -ForegroundColor Cyan
do {
    try {
        $null = docker ps
        $dockerRunning = $?
    } catch {
        $dockerRunning = $false
    }

    if (-not $dockerRunning) {
        Write-Host "Docker服务尚未就绪，等待5秒..." -ForegroundColor Yellow
        Start-Sleep -Seconds 5
    }
} while (-not $dockerRunning)

# 构建或导出worker镜像
$workerImageExists = docker images -q veins-worker 2>$null
if ($workerImageExists) {
    Write-Host "检测到veins-worker镜像已存在，导出镜像..." -ForegroundColor Green
} else {
    Write-Host "构建worker镜像..." -ForegroundColor Cyan
    Push-Location -Path .\worker
    docker build -t veins-worker .
    if ($LASTEXITCODE -ne 0) {
        Write-Host "worker镜像构建失败，请检查错误信息。" -ForegroundColor Red
        Pop-Location
        Pause
        exit
    }
    Pop-Location
}

# 导出worker镜像
Write-Host "导出worker镜像为tar文件..." -ForegroundColor Cyan
docker save -o veins-worker.tar veins-worker
if ($LASTEXITCODE -ne 0) {
    Write-Host "worker镜像导出失败，请检查错误信息。" -ForegroundColor Red
    Pause
    exit
}

# 构建后端镜像
Write-Host "构建后端Docker镜像..." -ForegroundColor Cyan
docker build -t veins-iov-simulator-backend .

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n安装成功！您现在可以使用run.ps1脚本运行应用。`n" -ForegroundColor Green

    # 清理临时文件
    if (Test-Path -Path .\veins-worker.tar) {
        Write-Host "清理临时文件..." -ForegroundColor Cyan
        Remove-Item -Path .\veins-worker.tar -Force
    }
} else {
    Write-Host "`n安装失败，请检查错误信息。`n" -ForegroundColor Red
}

Pause
