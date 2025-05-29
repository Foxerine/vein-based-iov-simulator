# PowerShell运行脚本
Write-Host "启动 Veins IOV Simulator 后端..." -ForegroundColor Cyan

# 检查数据卷是否存在
if (-not (Test-Path -Path "config.cfg")) {
    Write-Host "错误：找不到config.cfg文件！" -ForegroundColor Red
    Write-Host "请确保在当前目录中有正确配置的config.cfg文件。" -ForegroundColor Yellow
    Pause
    exit
}

# 检查是否有运行中的同名容器
$containerExists = docker ps -a | Select-String "veins-simulator"
if ($containerExists) {
    Write-Host "已在运行..." -ForegroundColor Yellow
    Pause
    exit
}

# 创建用户项目目录（如果不存在）
if (-not (Test-Path -Path "user_projects")) {
    New-Item -Path "user_projects" -ItemType Directory | Out-Null
}

Write-Host "启动容器..." -ForegroundColor Cyan
docker run -d `
  -p 8000:8000 `
  -v "${PWD}/data.db:/app/data.db" `
  -v "${PWD}/config.cfg:/app/config.cfg" `
  -v "${PWD}/user_projects:/app/user_projects" `
  --privileged `
  --name veins-simulator `
  veins-iov-simulator-backend

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n服务启动成功！" -ForegroundColor Green
    Write-Host "API现在可以通过 http://localhost:8000 访问。`n" -ForegroundColor Green
} else {
    Write-Host "`n服务启动失败，请检查错误信息。`n" -ForegroundColor Red
}

Pause
