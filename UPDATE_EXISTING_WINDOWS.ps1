param(
    [string]$RevivalRepo = "$env:USERPROFILE\Documents\kinkerm-csgo-revival-pinned",
    [string]$CsgoGcSource = "$env:USERPROFILE\Documents\csgo_gc_clean",
    [string]$CsgoDir = "C:\Program Files (x86)\Steam\steamapps\common\csgo legacy",
    [switch]$SkipBuild,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Branch = "operation-revival-finish"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = Join-Path "$env:USERPROFILE\Documents\CSGO_Revival_Backups" $stamp

function Need-Path([string]$Path, [string]$Label) {
    if (-not (Test-Path $Path)) { throw "$Label not found: $Path" }
}

Need-Path $RevivalRepo "Revival repo"
Need-Path (Join-Path $RevivalRepo ".git") "Revival git checkout"
Need-Path $CsgoGcSource "csgo_gc source"
Need-Path (Join-Path $CsgoGcSource "csgo_gc") "csgo_gc source subfolder"

Write-Host ""
Write-Host "=== CS:GO Revival safe update ===" -ForegroundColor Cyan
Write-Host "Repo:       $RevivalRepo"
Write-Host "csgo_gc:    $CsgoGcSource"
Write-Host "CS:GO root: $CsgoDir"
Write-Host "Backup:     $backup"
Write-Host ""

New-Item $backup -ItemType Directory -Force | Out-Null

# Preserve all per-server/player state and the user's working launcher config.
$dataDir = Join-Path $RevivalRepo "server\data"
if (Test-Path $dataDir) {
    Copy-Item $dataDir (Join-Path $backup "data") -Recurse -Force
}
$launcherCfg = Join-Path $RevivalRepo "launcher\launcher.cfg"
if (Test-Path $launcherCfg) {
    Copy-Item $launcherCfg (Join-Path $backup "launcher.cfg") -Force
}

Write-Host "[1/6] Updating code to latest $Branch..." -ForegroundColor Yellow
& git -C $RevivalRepo fetch origin $Branch
if ($LASTEXITCODE -ne 0) { throw "git fetch failed" }
& git -C $RevivalRepo checkout -B $Branch "origin/$Branch"
if ($LASTEXITCODE -ne 0) { throw "git checkout failed" }
& git -C $RevivalRepo reset --hard "origin/$Branch"
if ($LASTEXITCODE -ne 0) { throw "git reset failed" }

# Restore state explicitly even though ignored/untracked files normally survive checkout.
if (Test-Path (Join-Path $backup "data")) {
    New-Item $dataDir -ItemType Directory -Force | Out-Null
    Copy-Item (Join-Path $backup "data\*") $dataDir -Recurse -Force
}
if (Test-Path (Join-Path $backup "launcher.cfg")) {
    New-Item (Split-Path $launcherCfg) -ItemType Directory -Force | Out-Null
    Copy-Item (Join-Path $backup "launcher.cfg") $launcherCfg -Force
}

$head = (& git -C $RevivalRepo rev-parse HEAD).Trim()
Write-Host "    HEAD = $head"

Write-Host "[2/6] Applying complete csgo_gc overlay..." -ForegroundColor Yellow
Copy-Item (Join-Path $RevivalRepo "csgo_gc-patch\*") (Join-Path $CsgoGcSource "csgo_gc\") -Recurse -Force

if (-not $SkipBuild) {
    Write-Host "[3/6] Building csgo + srcds + csgo_gc (Win32 Release)..." -ForegroundColor Yellow
    Need-Path (Join-Path $CsgoGcSource "build") "Existing CMake build directory"
    & cmake --build (Join-Path $CsgoGcSource "build") --config Release --target csgo srcds csgo_gc
    if ($LASTEXITCODE -ne 0) { throw "csgo_gc build failed" }
} else {
    Write-Host "[3/6] Build skipped by request."
}

$pack = Join-Path $RevivalRepo "launcher\csgo-revival-pack.zip"
Write-Host "[4/6] Building full pack including Panorama..." -ForegroundColor Yellow
& py -3 (Join-Path $RevivalRepo "launcher\build_pack.py") --csgo-gc-dir $CsgoGcSource --out $pack
if ($LASTEXITCODE -ne 0) { throw "pack build failed" }
Need-Path $pack "Built revival pack"

if (-not $SkipInstall) {
    Write-Host "[5/6] Installing pack into CS:GO Legacy..." -ForegroundColor Yellow
    Need-Path $CsgoDir "CS:GO Legacy root"
    Expand-Archive -Path $pack -DestinationPath $CsgoDir -Force
} else {
    Write-Host "[5/6] Client install skipped by request."
}

Write-Host "[6/6] Validating installed queue UI..." -ForegroundColor Yellow
if (-not $SkipInstall) {
    $repoUi = Join-Path $RevivalRepo "panorama\layout\mainmenu_play.xml"
    $gameUi = Join-Path $CsgoDir "csgo\panorama\layout\mainmenu_play.xml"
    Need-Path $repoUi "Repo Panorama layout"
    Need-Path $gameUi "Installed Panorama layout"

    $a = (Get-FileHash $repoUi -Algorithm SHA256).Hash
    $b = (Get-FileHash $gameUi -Algorithm SHA256).Hash
    if ($a -ne $b) {
        throw "Panorama validation failed: installed mainmenu_play.xml does not match the repo"
    }

    Need-Path (Join-Path $CsgoDir "csgo_gc.dll") "Installed csgo_gc.dll"
    Need-Path (Join-Path $CsgoDir "csgo_revival.exe") "Installed revival launcher"
    Write-Host "    Panorama + runtime validation OK." -ForegroundColor Green
}

Write-Host ""
Write-Host "UPDATE COMPLETE" -ForegroundColor Green
Write-Host "Preserved server\data and launcher\launcher.cfg."
Write-Host "Backend code, GC overlay, full Panorama UI, config, and items_game are now from:"
Write-Host "  $head"
Write-Host ""
Write-Host "Laptop: copy deploy\windows-gameserver\agent.py from this repo to the laptop,"
Write-Host "and copy the newly built srcds.exe + csgo_gc.dll/config/items_game as before."
