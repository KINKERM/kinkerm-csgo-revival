param(
    [string]$RevivalRepo = "$env:USERPROFILE\Documents\kinkerm-csgo-revival-pinned",
    [string]$CsgoGcSource = "$env:USERPROFILE\Documents\csgo_gc_clean",
    [string]$CsgoDir = "C:\Program Files (x86)\Steam\steamapps\common\csgo legacy",
    [switch]$ForceBuild,
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
$csgoGcHead = (& git -C $CsgoGcSource rev-parse HEAD).Trim()
Write-Host "csgo_gc HEAD: $csgoGcHead"
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

# Decide whether the COMPILED GC/runtime matches the target patch tree.
# Comparing current repo HEAD to origin is not sufficient: the user may have
# already pulled/reset before running this script while still having older
# binaries. Persist the tree hash that was actually built instead.
$targetGcTree = (& git -C $RevivalRepo rev-parse "origin/${Branch}:csgo_gc-patch").Trim()
$targetHookPatch = (& git -C $RevivalRepo rev-parse "origin/${Branch}:tools/patch_steam_hook.py").Trim()
if ((-not $targetGcTree) -or (-not $targetHookPatch)) {
    throw "Could not resolve target GC patch inputs."
}
$targetGcTree = "$targetGcTree-$targetHookPatch"

$buildStamp = Join-Path $CsgoGcSource "build\.revival_gc_patch_tree.txt"
$lastBuiltGcTree = ""
if (Test-Path $buildStamp) {
    $lastBuiltGcTree = (Get-Content $buildStamp -Raw -ErrorAction SilentlyContinue).Trim()
}
$gcSourceChanged = ($lastBuiltGcTree -ne $targetGcTree)

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

# Restore steam_hook.cpp from THIS csgo_gc source tree's own commit.
# This keeps it ABI/compiler-compatible with the existing CMake project. The
# revival patcher below makes only the two small required edits in-place.
& git -C $CsgoGcSource checkout -- `
    "csgo_gc/steam_hook.cpp" `
    "csgo_gc/platform.h" `
    "csgo_gc/platform_windows.cpp"
if ($LASTEXITCODE -ne 0) {
    throw "Could not restore local csgo_gc steam_hook/platform files."
}

Copy-Item (Join-Path $RevivalRepo "csgo_gc-patch\*") (Join-Path $CsgoGcSource "csgo_gc\") -Recurse -Force

$steamHook = Join-Path $CsgoGcSource "csgo_gc\steam_hook.cpp"
& py -3 (Join-Path $RevivalRepo "tools\patch_steam_hook.py") $steamHook
if ($LASTEXITCODE -ne 0) {
    throw "Failed to patch the local-compatible steam_hook.cpp."
}

$clientExe = Join-Path $CsgoGcSource "build\launcher\Release\csgo.exe"
$serverExe = Join-Path $CsgoGcSource "build\launcher\Release\srcds.exe"
$gcDll = Join-Path $CsgoGcSource "build\csgo_gc\Release\csgo_gc.dll"
$existingRuntime = (Test-Path $clientExe) -and (Test-Path $serverExe) -and (Test-Path $gcDll)
$autoReuseBuild = (-not $SkipBuild) -and (-not $ForceBuild) -and (-not $gcSourceChanged) -and $existingRuntime
$didBuild = $false

if ((-not $SkipBuild) -and (-not $autoReuseBuild)) {
    Write-Host "[3/6] Building csgo + srcds + csgo_gc (Win32 Release)..." -ForegroundColor Yellow
    if ($ForceBuild) {
        Write-Host "    ForceBuild requested; rebuilding regardless of build stamp." -ForegroundColor Yellow
    } elseif ($gcSourceChanged) {
        if ($lastBuiltGcTree) {
            Write-Host "    Built GC tree $lastBuiltGcTree does not match target $targetGcTree; rebuilding."
        } else {
            Write-Host "    No compiled-GC stamp exists yet; rebuilding once to guarantee the DLL/EXEs match."
        }
    } elseif (-not $existingRuntime) {
        Write-Host "    Existing Release runtime is incomplete, so a rebuild is required."
    }
    Need-Path (Join-Path $CsgoGcSource "build") "Existing CMake build directory"

    if ($ForceBuild) {
        Write-Host "    Cleaning stale C++ objects first..." -ForegroundColor Yellow
        & cmake --build (Join-Path $CsgoGcSource "build") --config Release --target clean
        if ($LASTEXITCODE -ne 0) { throw "csgo_gc clean failed" }
    }

    & cmake --build (Join-Path $CsgoGcSource "build") --config Release --target csgo srcds csgo_gc
    if ($LASTEXITCODE -ne 0) { throw "csgo_gc build failed" }

    $gcDllBytes = [IO.File]::ReadAllBytes($gcDll)
    $gcDllText = [Text.Encoding]::ASCII.GetString($gcDllBytes)
    if (-not $gcDllText.Contains("REVIVAL_MM_BRIDGE_CLEAN_V1")) {
        throw "Built csgo_gc.dll does not contain the current client matchmaking source. Stale object files are still being used."
    }
    if (-not $gcDllText.Contains("REVIVAL_SERVER_RESERVATION_RETRY_V4")) {
        throw "Built csgo_gc.dll does not contain the current server reservation handshake fix."
    }
    if (-not $gcDllText.Contains("REVIVAL_SERVER_GC_OFFLINE_DELIVERY_V1")) {
        throw "Built csgo_gc.dll does not contain the local server-GC delivery fix."
    }
    if (-not $gcDllText.Contains("REVIVAL_SERVER_ID_EXPORT_V1")) {
        throw "Built csgo_gc.dll does not contain the real game-server SteamID export."
    }
    if (-not $gcDllText.Contains("REVIVAL_CLIENT_COOKIE_RESERVE_V3")) {
        throw "Built csgo_gc.dll does not contain the current client cookie-reservation fix."
    }
    if (-not $gcDllText.Contains("REVIVAL_CLIENT_DIRECT_UDP_V1")) {
        throw "Built csgo_gc.dll does not contain the direct-UDP matchmaking reserve fix."
    }
    if (-not $gcDllText.Contains("REVIVAL_CLIENT_READY_FLOW_V1")) {
        throw "Built csgo_gc.dll does not contain the stock match-ready flow fix."
    }
    if (-not $gcDllText.Contains("REVIVAL_CLIENT_ACCEPT_WATCH_V1")) {
        throw "Built csgo_gc.dll does not contain the stock Accept stage watcher."
    }
    if (-not $gcDllText.Contains("REVIVAL_CLIENT_DIRECT_ACCEPT_ROUTE_V2")) {
        throw "Built csgo_gc.dll does not contain the direct Accept routing fix."
    }
    if (-not $gcDllText.Contains("REVIVAL_SERVER_ACCEPT_ROSTER_V1")) {
        throw "Built csgo_gc.dll does not contain the server Accept roster path."
    }
    if (-not $gcDllText.Contains("REVIVAL_ENGINE_QUEUE_RESERVE_V1")) {
        throw "Built csgo_gc.dll does not contain the engine queued-reservation bridge."
    }
    if (-not $gcDllText.Contains("REVIVAL_SERVER_LOCAL_SOCACHE_V1")) {
        throw "Built csgo_gc.dll does not contain local server SOCache injection."
    }
    if (-not $gcDllText.Contains("REVIVAL_SERVER_LOCAL_SOCACHE_AUTH_V1")) {
        throw "Built csgo_gc.dll does not contain local SOCache auth trigger."
    }
    if (-not $gcDllText.Contains("REVIVAL_SERVER_PLAYER_AUTH_V1")) {
        throw "Built csgo_gc.dll does not contain authoritative player-auth marker support."
    }
    if (-not $gcDllText.Contains("REVIVAL_SERVER_REWARD_BRIDGE_V1")) {
        throw "Built csgo_gc.dll does not contain the direct match-end server reward bridge."
    }
    if (-not $gcDllText.Contains("REVIVAL_CLIENT_REWARD_BRIDGE_V1")) {
        throw "Built csgo_gc.dll does not contain the direct match-end client reward bridge."
    }
    if (-not $gcDllText.Contains("REVIVAL_GUARANTEED_MATCH_DROPS_V1")) {
        throw "Built csgo_gc.dll does not contain guaranteed Competitive match drops."
    }
    if (-not $gcDllText.Contains("REVIVAL_SYNTHETIC_MATCH_END_V1")) {
        throw "Built csgo_gc.dll does not contain completed-match result fallback."
    }
    if (-not $gcDllText.Contains("REVIVAL_NATIVE_DROP_REVEAL_V1")) {
        throw "Built csgo_gc.dll does not contain native CCSGameRules drop reveal."
    }
    if (-not $gcDllText.Contains("REVIVAL_NATIVE_DROP_BUNDLE_V1")) {
        throw "Built csgo_gc.dll does not contain exact server drop bundle delivery."
    }
    if (-not $gcDllText.Contains("REVIVAL_SERVER_DROP_IMPORT_V1")) {
        throw "Built csgo_gc.dll does not contain exact server item persistence."
    }
    Write-Host "    Verified current client + server matchmaking code is inside csgo_gc.dll." -ForegroundColor Green

    [System.IO.File]::WriteAllText(
        $buildStamp,
        $targetGcTree + [Environment]::NewLine,
        (New-Object System.Text.UTF8Encoding($false))
    )
    Write-Host "    Recorded built GC patch tree: $targetGcTree" -ForegroundColor DarkGray
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
    Write-Host "[5/6] Installing pack + rebuilding Panorama PBIN..." -ForegroundColor Yellow
    Need-Path $CsgoDir "CS:GO Legacy root"
    Expand-Archive -Path $pack -DestinationPath $CsgoDir -Force

    # Do not trust ZIP extraction alone for the runtime binaries. Explicitly
    # overwrite them from the freshly built Release outputs so a stale/locked
    # DLL can never silently survive an update.
    Write-Host "    Installing verified fresh runtime binaries..." -ForegroundColor Yellow
    $gcRuntimeDir = Join-Path $CsgoDir "csgo_gc"
    New-Item $gcRuntimeDir -ItemType Directory -Force | Out-Null
    Copy-Item $gcDll (Join-Path $gcRuntimeDir "csgo_gc.dll") -Force
    Copy-Item $serverExe (Join-Path $CsgoDir "srcds.exe") -Force
    Copy-Item $clientExe (Join-Path $CsgoDir "csgo_revival.exe") -Force

    # Older broken revival packs put csgo_gc.dll at the game root. The launcher
    # never loads it, so remove it to avoid misleading future diagnostics.
    $wrongRootGc = Join-Path $CsgoDir "csgo_gc.dll"
    if (Test-Path $wrongRootGc) {
        Remove-Item $wrongRootGc -Force
    }

    $repackScript = Join-Path $RevivalRepo "REPACK_PANORAMA.ps1"
    Need-Path $repackScript "Panorama PBIN repack script"
    & powershell -ExecutionPolicy Bypass -File $repackScript -RevivalRepo $RevivalRepo -CsgoDir $CsgoDir
    if ($LASTEXITCODE -ne 0) {
        throw "Panorama PBIN repack failed with exit code $LASTEXITCODE"
    }
} else {
    Write-Host "[5/6] Client install skipped by request."
}

Write-Host "[6/6] Validating packed Panorama + runtime..." -ForegroundColor Yellow
if (-not $SkipInstall) {
    $panoramaDir = Join-Path $CsgoDir "csgo\panorama"
    Need-Path (Join-Path $panoramaDir "code.pbin") "Rebuilt code.pbin"
    Need-Path (Join-Path $panoramaDir "_code.pbin") "Preserved _code.pbin"
    Need-Path (Join-Path $panoramaDir "pbin.py") "Installed pbin.py"
    Need-Path (Join-Path $CsgoDir "bin\panorama.dll") "Patched panorama.dll"
    $installedGc = Join-Path $CsgoDir "csgo_gc\csgo_gc.dll"
    $installedServer = Join-Path $CsgoDir "srcds.exe"
    $installedLauncher = Join-Path $CsgoDir "csgo_revival.exe"

    Need-Path $installedGc "Installed csgo_gc.dll"
    Need-Path $installedServer "Installed srcds.exe"
    Need-Path $installedLauncher "Installed revival launcher"

    $builtGcHash = (Get-FileHash $gcDll -Algorithm SHA256).Hash
    $installedGcHash = (Get-FileHash $installedGc -Algorithm SHA256).Hash
    if ($builtGcHash -ne $installedGcHash) {
        throw "Installed csgo_gc.dll does not match the freshly built DLL."
    }

    $builtServerHash = (Get-FileHash $serverExe -Algorithm SHA256).Hash
    $installedServerHash = (Get-FileHash $installedServer -Algorithm SHA256).Hash
    if ($builtServerHash -ne $installedServerHash) {
        throw "Installed srcds.exe does not match the freshly built server launcher."
    }

    $builtLauncherHash = (Get-FileHash $clientExe -Algorithm SHA256).Hash
    $installedLauncherHash = (Get-FileHash $installedLauncher -Algorithm SHA256).Hash
    if ($builtLauncherHash -ne $installedLauncherHash) {
        throw "Installed csgo_revival.exe does not match the freshly built client launcher."
    }

    $installedGcBytes = [IO.File]::ReadAllBytes($installedGc)
    $installedGcText = [Text.Encoding]::ASCII.GetString($installedGcBytes)
    if (-not $installedGcText.Contains("REVIVAL_MM_BRIDGE_CLEAN_V1")) {
        throw "Installed csgo_gc.dll is missing the current client matchmaking build marker."
    }
    if (-not $installedGcText.Contains("REVIVAL_SERVER_RESERVATION_RETRY_V4")) {
        throw "Installed csgo_gc.dll is missing the current server reservation handshake fix."
    }
    if (-not $installedGcText.Contains("REVIVAL_SERVER_GC_OFFLINE_DELIVERY_V1")) {
        throw "Installed csgo_gc.dll is missing the local server-GC delivery fix."
    }
    if (-not $installedGcText.Contains("REVIVAL_SERVER_ID_EXPORT_V1")) {
        throw "Installed csgo_gc.dll is missing the real game-server SteamID export."
    }
    if (-not $installedGcText.Contains("REVIVAL_CLIENT_COOKIE_RESERVE_V3")) {
        throw "Installed csgo_gc.dll is missing the current client cookie-reservation fix."
    }
    if (-not $installedGcText.Contains("REVIVAL_CLIENT_DIRECT_UDP_V1")) {
        throw "Installed csgo_gc.dll is missing the direct-UDP matchmaking reserve fix."
    }
    if (-not $installedGcText.Contains("REVIVAL_CLIENT_READY_FLOW_V1")) {
        throw "Installed csgo_gc.dll is missing the stock match-ready flow fix."
    }
    if (-not $installedGcText.Contains("REVIVAL_CLIENT_ACCEPT_WATCH_V1")) {
        throw "Installed csgo_gc.dll is missing the stock Accept stage watcher."
    }
    if (-not $installedGcText.Contains("REVIVAL_CLIENT_DIRECT_ACCEPT_ROUTE_V2")) {
        throw "Installed csgo_gc.dll is missing the direct Accept routing fix."
    }
    if (-not $installedGcText.Contains("REVIVAL_SERVER_ACCEPT_ROSTER_V1")) {
        throw "Installed csgo_gc.dll is missing the server Accept roster path."
    }
    if (-not $installedGcText.Contains("REVIVAL_ENGINE_QUEUE_RESERVE_V1")) {
        throw "Installed csgo_gc.dll is missing the engine queued-reservation bridge."
    }
    if (-not $installedGcText.Contains("REVIVAL_SERVER_LOCAL_SOCACHE_V1")) {
        throw "Installed csgo_gc.dll is missing local server SOCache injection."
    }
    if (-not $installedGcText.Contains("REVIVAL_SERVER_LOCAL_SOCACHE_AUTH_V1")) {
        throw "Installed csgo_gc.dll is missing local SOCache auth trigger."
    }
    if (-not $installedGcText.Contains("REVIVAL_SERVER_PLAYER_AUTH_V1")) {
        throw "Installed csgo_gc.dll is missing authoritative player-auth marker support."
    }
    if (-not $installedGcText.Contains("REVIVAL_SERVER_REWARD_BRIDGE_V1")) {
        throw "Installed csgo_gc.dll is missing the direct match-end server reward bridge."
    }
    if (-not $installedGcText.Contains("REVIVAL_CLIENT_REWARD_BRIDGE_V1")) {
        throw "Installed csgo_gc.dll is missing the direct match-end client reward bridge."
    }
    if (-not $installedGcText.Contains("REVIVAL_GUARANTEED_MATCH_DROPS_V1")) {
        throw "Installed csgo_gc.dll is missing guaranteed Competitive match drops."
    }
    if (-not $installedGcText.Contains("REVIVAL_SYNTHETIC_MATCH_END_V1")) {
        throw "Installed csgo_gc.dll is missing completed-match result fallback."
    }
    if (-not $installedGcText.Contains("REVIVAL_NATIVE_DROP_REVEAL_V1")) {
        throw "Installed csgo_gc.dll is missing native CCSGameRules drop reveal."
    }
    if (-not $installedGcText.Contains("REVIVAL_NATIVE_DROP_BUNDLE_V1")) {
        throw "Installed csgo_gc.dll is missing exact server drop bundle delivery."
    }
    if (-not $installedGcText.Contains("REVIVAL_SERVER_DROP_IMPORT_V1")) {
        throw "Installed csgo_gc.dll is missing exact server item persistence."
    }

    Write-Host "    Installed runtime hashes match freshly built outputs." -ForegroundColor Green
    Write-Host "    PBIN Panorama + runtime validation OK." -ForegroundColor Green
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
