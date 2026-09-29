$ErrorActionPreference = "Stop"

$Repo = "C:\Users\mkrob\Documents\kinkerm-csgo-revival-pinned"
$GcDir = "C:\Users\mkrob\Documents\csgo_gc_clean"
$Pack = Join-Path $Repo "launcher\csgo-revival-pack.zip"
$Downloads = Join-Path $env:USERPROFILE "Downloads\csgo-revival-pack.zip"

if (-not (Test-Path (Join-Path $Repo "launcher\build_pack.py"))) {
    throw "Repo not found: $Repo"
}
if (-not (Test-Path $GcDir)) {
    throw "GC build tree not found: $GcDir"
}

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) { throw "python.exe was not found on PATH." }

Write-Host "=== SAFE REVIVAL PACK REBUILD ===" -ForegroundColor Cyan
Write-Host "Using restored known-good pack builder (no items_game schema rewrite)." -ForegroundColor Yellow

Remove-Item $Pack -Force -ErrorAction SilentlyContinue

Push-Location $Repo
try {
    & $python.Source ".\launcher\build_pack.py" "--csgo-gc-dir" $GcDir "--out" $Pack
    if ($LASTEXITCODE -ne 0) {
        throw "build_pack.py failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path $Pack)) {
    throw "Pack was not created: $Pack"
}

Copy-Item $Pack $Downloads -Force

Write-Host ""
Write-Host "SAFE PACK BUILT:" -ForegroundColor Green
Write-Host "  $Pack"
Write-Host "COPIED TO:" -ForegroundColor Green
Write-Host "  $Downloads"
Write-Host ""
Write-Host "IMPORTANT: replace the currently installed broken pack with this ZIP." -ForegroundColor Cyan
