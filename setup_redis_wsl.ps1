# Скрипт для установки Redis в WSL2
# Запустите в PowerShell от администратора

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Установка Redis в WSL2" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Проверка WSL
Write-Host "`nПроверка WSL..." -ForegroundColor Yellow
$wslCheck = wsl --list --quiet 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ WSL не установлен!" -ForegroundColor Red
    Write-Host "Установите WSL2 командой: wsl --install" -ForegroundColor Yellow
    exit 1
}

Write-Host "✓ WSL найден" -ForegroundColor Green

# Установка Redis в WSL
Write-Host "`nУстановка Redis в WSL..." -ForegroundColor Yellow
wsl bash -c "sudo apt update && sudo apt install -y redis-server"

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ Redis установлен" -ForegroundColor Green
} else {
    Write-Host "❌ Ошибка установки Redis" -ForegroundColor Red
    exit 1
}

# Запуск Redis
Write-Host "`nЗапуск Redis..." -ForegroundColor Yellow
wsl bash -c "sudo service redis-server start"

# Проверка
Write-Host "`nПроверка подключения..." -ForegroundColor Yellow
$pingResult = wsl bash -c "redis-cli ping" 2>&1

if ($pingResult -match "PONG") {
    Write-Host "✓ Redis запущен и работает!" -ForegroundColor Green
    Write-Host "`nТеперь запустите: python manage.py test_cache" -ForegroundColor Cyan
} else {
    Write-Host "❌ Redis не отвечает" -ForegroundColor Red
    Write-Host "Попробуйте запустить вручную: wsl sudo service redis-server start" -ForegroundColor Yellow
}

Write-Host "`n========================================" -ForegroundColor Cyan

