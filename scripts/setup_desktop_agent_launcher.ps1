# Cria atalho na área de trabalho para o Agente Diário local (BSA/BSB → rascunho).
param(
    [string]$Comps = "BSA,BSB",
    [string]$Planejador = "llm"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ConfigDir = Join-Path $env:USERPROFILE ".palpitaria"
$ConfigPath = Join-Path $ConfigDir "agent_diario.json"
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Launcher = Join-Path $RepoRoot "scripts\palpitaria_agente_diario.py"
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Palpitaria - Agente Diario.lnk"

if (-not (Test-Path $Python)) {
    Write-Host "Python do venv nao encontrado: $Python" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $Launcher)) {
    Write-Host "Launcher nao encontrado: $Launcher" -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
$config = @{
    comps      = $Comps
    planejador = $Planejador
    sem_narrar = $false
    skip_sync  = $false
} | ConvertTo-Json
Set-Content -Path $ConfigPath -Value $config -Encoding UTF8

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Python
$Shortcut.Arguments = "`"$Launcher`""
$Shortcut.WorkingDirectory = $RepoRoot
$IconIco = Join-Path $RepoRoot "src\palpitaria\static\assets\launcher.ico"
$IconPng = Join-Path $RepoRoot "src\palpitaria\static\assets\logo.png"
$IconPath = if (Test-Path $IconIco) { $IconIco } else { $IconPng }
$Shortcut.IconLocation = "$IconPath,0"
$Shortcut.Description = "Palpitaria FC - Agente Diario local (BSA/BSB, so rascunho)"
$Shortcut.Save()

Write-Host "Atalho criado: $ShortcutPath" -ForegroundColor Green
Write-Host "Config local: $ConfigPath"
Write-Host ""
Write-Host "Ao clicar: sync + analise + historico IA + rascunho (NAO publica)."
Write-Host "Logs em: $ConfigDir\logs\"
Write-Host ""
Write-Host "Diferenca do outro atalho:" -ForegroundColor Cyan
Write-Host "  Palpitaria - Atualizar     -> Cloud Run / pipeline remoto"
Write-Host "  Palpitaria - Agente Diario -> agente local no seu PC"
