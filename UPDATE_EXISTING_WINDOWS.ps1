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

$currentHead = (& git -C $RevivalRepo rev-parse HEAD).Trim()
if (-not $currentHead) { throw "Could not read current revival commit." }

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

# A setup ZIP/manual copy may have placed this updater in the checkout before the
# branch itself started tracking it. That leaves an untracked file at the exact
# path Git must create and makes checkout abort. Preserve it in the timestamped
# backup, then remove only that one conflicting untracked file.
$updaterRel = "UPDATE_EXISTING_WINDOWS.ps1"
& git -C $RevivalRepo ls-files --error-unmatch -- $updaterRel *> $null
$updaterTracked = ($LASTEXITCODE -eq 0)
$updaterPath = Join-Path $RevivalRepo $updaterRel
if ((-not $updaterTracked) -and (Test-Path $updaterPath)) {
    Copy-Item $updaterPath (Join-Path $backup "UPDATE_EXISTING_WINDOWS.pre-branch.ps1") -Force
    Remove-Item $updaterPath -Force
    Write-Host "    Removed conflicting untracked updater after backing it up." -ForegroundColor Yellow
}

# Decide whether the compiled GC/runtime actually changed before moving HEAD.
& git -C $RevivalRepo diff --quiet $currentHead "origin/$Branch" -- "csgo_gc-patch"
$gcDiffExit = $LASTEXITCODE
if ($gcDiffExit -eq 0) {
    $gcSourceChanged = $false
} elseif ($gcDiffExit -eq 1) {
    $gcSourceChanged = $true
} else {
    throw "Could not compare csgo_gc-patch between $currentHead and origin/$Branch"
}

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

$clientExe = Join-Path $CsgoGcSource "build\launcher\Release\csgo.exe"
$serverExe = Join-Path $CsgoGcSource "build\launcher\Release\srcds.exe"
$gcDll = Join-Path $CsgoGcSource "build\csgo_gc\Release\csgo_gc.dll"
$existingRuntime = (Test-Path $clientExe) -and (Test-Path $serverExe) -and (Test-Path $gcDll)
$autoReuseBuild = (-not $SkipBuild) -and (-not $gcSourceChanged) -and $existingRuntime
$didBuild = $false

if ((-not $SkipBuild) -and (-not $autoReuseBuild)) {
    Write-Host "[3/6] Building csgo + srcds + csgo_gc (Win32 Release)..." -ForegroundColor Yellow
    if ($gcSourceChanged) {
        Write-Host "    csgo_gc-patch changed since $currentHead, so a rebuild is required."
    } elseif (-not $existingRuntime) {
        Write-Host "    Existing Release runtime is incomplete, so a rebuild is required."
    }
    Need-Path (Join-Path $CsgoGcSource "build") "Existing CMake build directory"
    & cmake --build (Join-Path $CsgoGcSource "build") --config Release --target csgo srcds csgo_gc
    if ($LASTEXITCODE -ne 0) { throw "csgo_gc build failed" }
    $didBuild = $true
} elseif ($autoReuseBuild) {
    Write-Host "[3/6] No csgo_gc source changes; reusing your existing Release DLL/EXEs." -ForegroundColor Green
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

    # Mount the revival content root ahead of Valve stock VPK content.
    $gameInfo = Join-Path $CsgoDir "csgo\gameinfo.txt"
    Need-Path $gameInfo "CS:GO gameinfo.txt"
    $gameInfoText = [IO.File]::ReadAllText($gameInfo)

    if (-not (Test-Path (Join-Path $backup "gameinfo.txt.pre-kinkerm-revival"))) {
        Copy-Item $gameInfo (Join-Path $backup "gameinfo.txt.pre-kinkerm-revival") -Force
    }

    # Remove any older revival mount wherever it was and reinsert it as the
    # first SearchPaths entry so it wins before stock csgo/pak01 resources.
    $gameInfoText = [Text.RegularExpressions.Regex]::Replace(
        $gameInfoText,
        "(?im)^\s*Game(?:\+Mod)?\s+[^\r\n]*custom[\\/]kinkerm_revival[^\r\n]*\r?\n?",
        ""
    )

    $rx = New-Object Text.RegularExpressions.Regex(
        "SearchPaths\s*\{",
        [Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    $m = $rx.Match($gameInfoText)
    if (-not $m.Success) {
        throw "Could not find SearchPaths block in $gameInfo"
    }

    $mountLine = "`r`n`t`t`tGame`t`t|gameinfo_path|custom/kinkerm_revival"
    $gameInfoText = $gameInfoText.Insert($m.Index + $m.Length, $mountLine)
    [IO.File]::WriteAllText(
        $gameInfo,
        $gameInfoText,
        [Text.UTF8Encoding]::new($false)
    )
    Write-Host "    Mounted csgo\custom\kinkerm_revival FIRST in SearchPaths." -ForegroundColor Green
} else {
    Write-Host "[5/6] Client install skipped by request."
}

Write-Host "[6/6] Validating installed queue UI..." -ForegroundColor Yellow
if (-not $SkipInstall) {
    $gameInfo = Join-Path $CsgoDir "csgo\gameinfo.txt"
    Need-Path $gameInfo "CS:GO gameinfo.txt"
    $mountedGameInfo = [IO.File]::ReadAllText($gameInfo).Replace("\", "/")
    if (-not $mountedGameInfo.Contains("custom/kinkerm_revival")) {
        throw "Panorama validation failed: revival override is not mounted in gameinfo.txt"
    }
    $mountIndex = $mountedGameInfo.IndexOf("custom/kinkerm_revival")
    $stockIndex = $mountedGameInfo.IndexOf("|gameinfo_path|.")
    if (($stockIndex -ge 0) -and ($mountIndex -gt $stockIndex)) {
        throw "Panorama validation failed: revival mount is after stock game content"
    }
    Write-Host "    gameinfo SearchPaths priority OK." -ForegroundColor Green

    $uiFiles = @(
        "layout\mainmenu_play.xml",
        "scripts\mainmenu_play.js",
        "styles\mainmenu_play.css"
    )
    foreach ($rel in $uiFiles) {
        $repoUi = Join-Path (Join-Path $RevivalRepo "panorama") $rel
        $gameUi = Join-Path (Join-Path $CsgoDir "csgo\custom\kinkerm_revival\panorama") $rel
        Need-Path $repoUi "Repo Panorama file"
        Need-Path $gameUi "Mounted Panorama override"

        $repoHash = (Get-FileHash $repoUi -Algorithm SHA256).Hash
        $gameHash = (Get-FileHash $gameUi -Algorithm SHA256).Hash
        if ($repoHash -ne $gameHash) {
            throw "Panorama validation failed: mounted override $rel does not match the repo"
        }
    }

    Need-Path (Join-Path $CsgoDir "csgo_gc.dll") "Installed csgo_gc.dll"
    Need-Path (Join-Path $CsgoDir "csgo_revival.exe") "Installed revival launcher"
    Write-Host "    Mounted Panorama override + runtime validation OK." -ForegroundColor Green
}

Write-Host ""
Write-Host "UPDATE COMPLETE" -ForegroundColor Green
Write-Host "Preserved server\data and launcher\launcher.cfg."
Write-Host "Backend code, GC overlay, full Panorama UI, config, and items_game are now from:"
Write-Host "  $head"
Write-Host ""
Write-Host "Laptop: copy deploy\windows-gameserver\agent.py from this repo to the laptop."
if ($didBuild) {
    Write-Host "The GC/runtime was rebuilt, so also refresh laptop srcds.exe + csgo_gc.dll."
} else {
    Write-Host "The GC/runtime binary did not change in this update; your existing matching DLL/EXEs can stay."
}
Write-Host "Refresh laptop gc-config\config.txt and items_game.txt when setting/updating that server."
