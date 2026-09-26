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
if (Get-Process -Name "csgo" -ErrorAction SilentlyContinue) {
    throw "Close CS:GO completely before running this hotfix (Panorama code.pbin/panorama.dll must not be in use)."
}
$itemsGame = Join-Path $CsgoDir "csgo\scripts\items\items_game.txt"
$unusualCandidates = @(
    (Join-Path $CsgoGcSource "examples\unusual_loot_lists.txt"),
    (Join-Path $CsgoGcSource "csgo_gc\unusual_loot_lists.txt"),
    (Join-Path $CsgoGcSource "unusual_loot_lists.txt"),
    (Join-Path $CsgoDir "csgo_gc\unusual_loot_lists.txt")
)
$unusualLootLists = $unusualCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
Need-Path $itemsGame "CS:GO items_game.txt"
if (-not $unusualLootLists) {
    throw "unusual_loot_lists.txt not found. Checked: $($unusualCandidates -join '; ')"
}
Write-Host ("    Rare-special lists: " + $unusualLootLists) -ForegroundColor DarkGray

Write-Host ""
Write-Host "=== Matchmaking direct-UDP hotfix ===" -ForegroundColor Cyan

Write-Host "[1/5] Installing CS2-style 5-Covert recipe metadata..." -ForegroundColor Yellow
$tradeupPatcher = Join-Path $RevivalRepo "tools\patch_tradeup_items_game.py"
Need-Path $tradeupPatcher "Trade-up schema patcher"
& py -3 -m py_compile $tradeupPatcher
if ($LASTEXITCODE -ne 0) { throw "Trade-up schema patcher failed Python syntax preflight." }
& py -3 $tradeupPatcher $itemsGame --unusual-loot-lists $unusualLootLists
if ($LASTEXITCODE -ne 0) { throw "5-Covert trade-up items_game patch failed." }
& py -3 $tradeupPatcher $itemsGame --check
if ($LASTEXITCODE -ne 0) { throw "Installed 5-Covert client schema failed validation." }
$itemsText = Get-Content $itemsGame -Raw
if (-not $itemsText.Contains("REVIVAL_COVERT_TRADEUP_SCHEMA_V3")) {
    throw "Installed items_game.txt is missing REVIVAL_COVERT_TRADEUP_SCHEMA_V3"
}
Write-Host "    Valve-style recipes 5/15 + case gold-pool mappings installed." -ForegroundColor Green

# The GC itself loads this relative to the game working directory. A source
# checkout keeps it under examples\; release packages move it into csgo_gc\.
$runtimeDataDir = Join-Path $CsgoDir "csgo_gc"
New-Item $runtimeDataDir -ItemType Directory -Force | Out-Null
Copy-Item $unusualLootLists (Join-Path $runtimeDataDir "unusual_loot_lists.txt") -Force
Write-Host "    Installed csgo_gc\unusual_loot_lists.txt for runtime gold-pool resolution." -ForegroundColor Green

Write-Host "[2/5] Patching Legacy Panorama so Covert skins can actually be selected..." -ForegroundColor Yellow
$panoramaDir = Join-Path $CsgoDir "csgo\panorama"
$codePbin = Join-Path $panoramaDir "code.pbin"
$backupPbin = Join-Path $panoramaDir "_code.pbin"
$pbinTool = Join-Path $RevivalRepo "tools\pbin.py"
$tradeupUiTool = Join-Path $RevivalRepo "tools\patch_tradeup_panorama.py"
Need-Path $panoramaDir "CS:GO Panorama directory"
Need-Path $codePbin "CS:GO Panorama code.pbin"
Need-Path $pbinTool "PBIN tool"
Need-Path $tradeupUiTool "Trade-up Panorama patcher"

# Keep a one-time untouched/current baseline for manual recovery, but patch the
# ACTIVE code.pbin so any other revival Panorama changes already installed stay intact.
if (-not (Test-Path $backupPbin)) {
    Copy-Item $codePbin $backupPbin -Force
}

Push-Location $panoramaDir
try {
    & py -3 $pbinTool unpack "code.pbin"
    if ($LASTEXITCODE -ne 0) { throw "Could not unpack active Panorama code.pbin." }

    & py -3 $tradeupUiTool $panoramaDir
    if ($LASTEXITCODE -ne 0) { throw "Legacy Panorama Covert selector patch failed." }

    & py -3 $pbinTool pack
    if ($LASTEXITCODE -ne 0) { throw "Could not repack Panorama code.pbin." }

    & py -3 $pbinTool patch_panorama
    if ($LASTEXITCODE -ne 0) { throw "Could not patch panorama.dll for the modified code.pbin." }
}
finally {
    Pop-Location
}

$codeText = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($codePbin))
if (-not $codeText.Contains("REVIVAL_COVERT_TRADEUP_UI_V2")) {
    throw "Repacked code.pbin is missing REVIVAL_COVERT_TRADEUP_UI_V2"
}
Write-Host "    Panorama fallback bridge installed; native recipe metadata is now the primary eligibility path." -ForegroundColor Green

# Keep steam_hook.cpp matched to this local csgo_gc tree, then apply the
# current revival overlay and the small compatibility patch.
& git -C $CsgoGcSource checkout -- `
    "csgo_gc/steam_hook.cpp" `
    "csgo_gc/platform.h" `
    "csgo_gc/platform_windows.cpp"
if ($LASTEXITCODE -ne 0) { throw "Could not restore local steam_hook/platform files." }

Copy-Item (Join-Path $RevivalRepo "csgo_gc-patch\*") (Join-Path $CsgoGcSource "csgo_gc\") -Recurse -Force

$steamHook = Join-Path $CsgoGcSource "csgo_gc\steam_hook.cpp"
& py -3 (Join-Path $RevivalRepo "tools\patch_steam_hook.py") $steamHook
if ($LASTEXITCODE -ne 0) { throw "steam_hook patch failed." }

Write-Host "[3/5] Building only csgo_gc.dll..." -ForegroundColor Yellow
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
    "REVIVAL_CLIENT_DIRECT_UDP_V1",
    "REVIVAL_CLIENT_READY_FLOW_V1",
    "REVIVAL_CLIENT_ACCEPT_WATCH_V1",
    "REVIVAL_CLIENT_DIRECT_ACCEPT_ROUTE_V2",
    "REVIVAL_SERVER_ACCEPT_ROSTER_V1",
    "REVIVAL_ENGINE_QUEUE_RESERVE_V1",
    "REVIVAL_SERVER_LOCAL_SOCACHE_V1",
    "REVIVAL_SERVER_LOCAL_SOCACHE_AUTH_V1",
    "REVIVAL_SERVER_PLAYER_AUTH_V1",
    "REVIVAL_SERVER_REWARD_BRIDGE_V1",
    "REVIVAL_REWARD_SPOOL_QUEUE_V1",
    "REVIVAL_CLIENT_REWARD_BRIDGE_V1",
    "REVIVAL_GUARANTEED_MATCH_DROPS_V1",
    "REVIVAL_SYNTHETIC_MATCH_END_V1",
    "REVIVAL_NATIVE_DROP_REVEAL_V1",
    "REVIVAL_NATIVE_ENDMATCH_UI_V1",
    "REVIVAL_PROGRESS_BUNDLE_V2",
    "REVIVAL_SERVER_ITEM_AUTHORITY_V1",
    "REVIVAL_SERVER_DROP_IMPORT_V2",
    "REVIVAL_NATIVE_RANK_STATE_V2",
    "REVIVAL_NATIVE_ENDMATCH_CLIENT_UI_V3",
    "REVIVAL_CLIENT_USERMESSAGE_UI_V1",
    "REVIVAL_NATIVE_ENDMATCH_CLIENT_UI_V1",
    "REVIVAL_NATIVE_DROP_CRASH_GUARD_V1",
    "REVIVAL_NATIVE_DROP_TIMING_V3",
    "REVIVAL_NATIVE_DROP_BUNDLE_V1",
    "REVIVAL_SERVER_DROP_IMPORT_V1",
    "REVIVAL_COVERT_TRADEUP_V1"
)
foreach ($marker in $markers) {
    if (-not $blob.Contains($marker)) {
        throw "Fresh DLL is missing required marker $marker"
    }
}
Write-Host "    Fresh DLL contains direct-UDP matchmaking support." -ForegroundColor Green

Write-Host "[4/5] Installing DLL on main PC..." -ForegroundColor Yellow
$runtimeDir = Join-Path $CsgoDir "csgo_gc"
New-Item $runtimeDir -ItemType Directory -Force | Out-Null
Copy-Item $gcDll (Join-Path $runtimeDir "csgo_gc.dll") -Force
Remove-Item (Join-Path $CsgoDir "csgo_gc.dll") -Force -ErrorAction SilentlyContinue

$installed = Join-Path $runtimeDir "csgo_gc.dll"
if ((Get-FileHash $installed -Algorithm SHA256).Hash -ne (Get-FileHash $gcDll -Algorithm SHA256).Hash) {
    throw "Installed DLL hash does not match fresh build."
}

Write-Host "[5/5] Rebuilding laptop pack..." -ForegroundColor Yellow
$pack = Join-Path $RevivalRepo "launcher\csgo-revival-pack.zip"
& py -3 (Join-Path $RevivalRepo "launcher\build_pack.py") --csgo-gc-dir $CsgoGcSource --out $pack
if ($LASTEXITCODE -ne 0) { throw "pack build failed." }
Need-Path $pack "Laptop pack"

Write-Host ""
Write-Host "HOTFIX COMPLETE" -ForegroundColor Green
Write-Host ("DLL SHA256: " + (Get-FileHash $gcDll -Algorithm SHA256).Hash)
Write-Host "Laptop pack:"
Write-Host "  $pack"
