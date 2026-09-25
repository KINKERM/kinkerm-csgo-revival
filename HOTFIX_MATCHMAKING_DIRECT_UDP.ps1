param(
    [string]$RevivalRepo = "$env:USERPROFILE\Documents\kinkerm-csgo-revival-pinned",
    [string]$CsgoGcSource = "$env:USERPROFILE\Documents\csgo_gc_clean",
    [string]$CsgoDir = "C:\Program Files (x86)\Steam\steamapps\common\csgo legacy"
)

$ErrorActionPreference = "Stop"

function Need-Path([string]$Path, [string]$Label) {
    if (-not (Test-Path $Path)) { throw "$Label not found: $Path" }
}

Need-Path $RevivalRepo "Revival repo"
Need-Path $CsgoGcSource "csgo_gc source"
Need-Path (Join-Path $CsgoGcSource "build") "csgo_gc build directory"
Need-Path $CsgoDir "CS:GO Legacy root"

Write-Host ""
Write-Host "=== Matchmaking direct-UDP hotfix ===" -ForegroundColor Cyan

# Keep steam_hook.cpp matched to this local csgo_gc tree, then apply the
# current revival overlay and the small compatibility patch.
& git -C $CsgoGcSource checkout -- "csgo_gc/steam_hook.cpp"
if ($LASTEXITCODE -ne 0) { throw "Could not restore local steam_hook.cpp." }

Copy-Item (Join-Path $RevivalRepo "csgo_gc-patch\*") (Join-Path $CsgoGcSource "csgo_gc\") -Recurse -Force

$steamHook = Join-Path $CsgoGcSource "csgo_gc\steam_hook.cpp"
& py -3 (Join-Path $RevivalRepo "tools\patch_steam_hook.py") $steamHook
if ($LASTEXITCODE -ne 0) { throw "steam_hook patch failed." }

Write-Host "[1/3] Building only csgo_gc.dll..." -ForegroundColor Yellow
& cmake --build (Join-Path $CsgoGcSource "build") --config Release --target csgo_gc
if ($LASTEXITCODE -ne 0) { throw "csgo_gc build failed." }

$gcDll = Join-Path $CsgoGcSource "build\csgo_gc\Release\csgo_gc.dll"
Need-Path $gcDll "Built csgo_gc.dll"
$blob = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($gcDll))
$markers = @(
    "REVIVAL_MM_BRIDGE_CLEAN_V1",
    "REVIVAL_SERVER_RESERVATION_RETRY_V4",
    "REVIVAL_SERVER_GC_OFFLINE_DELIVERY_V1",
    "REVIVAL_CLIENT_COOKIE_RESERVE_V3",
    "REVIVAL_CLIENT_DIRECT_UDP_V1"
)
foreach ($marker in $markers) {
    if (-not $blob.Contains($marker)) {
        throw "Fresh DLL is missing required marker $marker"
    }
}
Write-Host "    Fresh DLL contains direct-UDP matchmaking support." -ForegroundColor Green

Write-Host "[2/3] Installing DLL on main PC..." -ForegroundColor Yellow
$runtimeDir = Join-Path $CsgoDir "csgo_gc"
New-Item $runtimeDir -ItemType Directory -Force | Out-Null
Copy-Item $gcDll (Join-Path $runtimeDir "csgo_gc.dll") -Force
Remove-Item (Join-Path $CsgoDir "csgo_gc.dll") -Force -ErrorAction SilentlyContinue

$installed = Join-Path $runtimeDir "csgo_gc.dll"
if ((Get-FileHash $installed -Algorithm SHA256).Hash -ne (Get-FileHash $gcDll -Algorithm SHA256).Hash) {
    throw "Installed DLL hash does not match fresh build."
}

Write-Host "[3/3] Rebuilding laptop pack..." -ForegroundColor Yellow
$pack = Join-Path $RevivalRepo "launcher\csgo-revival-pack.zip"
& py -3 (Join-Path $RevivalRepo "launcher\build_pack.py") --csgo-gc-dir $CsgoGcSource --out $pack
if ($LASTEXITCODE -ne 0) { throw "pack build failed." }
Need-Path $pack "Laptop pack"

Write-Host ""
Write-Host "HOTFIX COMPLETE" -ForegroundColor Green
Write-Host ("DLL SHA256: " + (Get-FileHash $gcDll -Algorithm SHA256).Hash)
Write-Host "Laptop pack:"
Write-Host "  $pack"
