# ============================================================
# start.ps1 — JARVIS Server Launcher
# Launches both the FastAPI backend and frontend static server
# in separate PowerShell windows simultaneously.
# ============================================================

# Resolve the directory where this script lives (h:\Jarvis)
$rootDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Paths to each server's working directory
$backendDir  = Join-Path $rootDir "jarvis"
$frontendDir = Join-Path $rootDir "frontend"

Write-Host ""
Write-Host "  =================================================================" -ForegroundColor Cyan
Write-Host "    J A R V I S  —  Starting All Servers" -ForegroundColor Cyan
Write-Host "  =================================================================" -ForegroundColor Cyan
Write-Host ""

# Verify the backend .env file has real API keys configured
$envFile = Join-Path $backendDir ".env"
if (Select-String -Path $envFile -Pattern "your_" -Quiet) {
    Write-Host "  ⚠️  WARNING: Your .env file still contains placeholder values!" -ForegroundColor Yellow
    Write-Host "     Edit $envFile and add your real API keys." -ForegroundColor Yellow
    Write-Host ""
}

# Launch the FastAPI backend server in a new window
Write-Host "  ▶  Starting Backend  (FastAPI)  → http://localhost:8000" -ForegroundColor Green
Start-Process powershell -ArgumentList `
    "-NoExit", `
    "-Command", `
    "Write-Host '[BACKEND] FastAPI Server' -ForegroundColor Cyan; cd '$backendDir'; python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload"

# Small delay so the backend process registers first
Start-Sleep -Milliseconds 500

# Launch the frontend static file server in a new window
Write-Host "  ▶  Starting Frontend (HTTP)     → http://localhost:3000" -ForegroundColor Green
Start-Process powershell -ArgumentList `
    "-NoExit", `
    "-Command", `
    "Write-Host '[FRONTEND] Static Server' -ForegroundColor Magenta; cd '$frontendDir'; python -m http.server 3000"

Write-Host ""
Write-Host "  ✅  Both servers are launching in separate windows." -ForegroundColor Green
Write-Host ""
Write-Host "  Access JARVIS at:" -ForegroundColor White
Write-Host "    🌐 UI       → http://localhost:3000" -ForegroundColor White
Write-Host "    🔌 API      → http://localhost:8000" -ForegroundColor White
Write-Host "    📖 API Docs → http://localhost:8000/docs" -ForegroundColor White
Write-Host ""
Write-Host "  Press Ctrl+C in each window to stop a server." -ForegroundColor DarkGray
Write-Host ""
