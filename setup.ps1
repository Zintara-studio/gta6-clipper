# Windows setup. Run once in PowerShell:  powershell -ExecutionPolicy Bypass -File .\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Need($cmd, $id) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
    Write-Host "Installing $id ..."
    winget install -e --id $id --accept-source-agreements --accept-package-agreements
  }
}
Need ffmpeg Gyan.FFmpeg
Need uv astral-sh.uv
Need node OpenJS.NodeJS.LTS

# pick up PATH changes from winget in this session
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

uv sync
Push-Location editor
npm install --no-audit --no-fund
Pop-Location
uv run clip init

Write-Host ""
Write-Host "Setup done. Next:"
Write-Host "  1. Edit config.yaml   (agent: claude | codex | agy)"
Write-Host "  2. Edit sources.yaml  (streamers / channels to clip)"
Write-Host "  3. Drop no-copyright music into assets\music\"
Write-Host "  4. Install ego lite and log into YOUR YouTube channel in it (see docs\CHANNEL_SETUP.md)"
Write-Host '  5. uv run clip run "<youtube or twitch url>"   then   uv run clip upload --check'
