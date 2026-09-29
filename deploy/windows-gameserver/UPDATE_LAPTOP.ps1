param(
    [Parameter(Mandatory = $true)]
    [string]$Pack,

    [string]$CsgoDir = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\csgo legacy",

    [string]$AgentDir = "C:\\CSGO-Revival-Agent"
)

$ErrorActionPreference = "Stop"
$Branch = "operation-revival-finish"
$RawBase = "https://raw.githubusercontent.com/KINKERM/kinkerm-csgo-revival/$Branch"

function Need-Path([string]$Path, [string]$Label) {
    if (-not (Test-Path $Path)) {
        throw "$Label not found: $Path"
    }
}

$Pack = (Resolve-Path $Pack).Path
Need-Path $Pack "Revival pack"
Need-Path $CsgoDir "CS:GO Legacy folder"
Need-Path (Join-Path $CsgoDir "csgo") "CS:GO csgo folder"

Write-Host ""
Write-Host "=== CS:GO Revival laptop update ===" -ForegroundColor Cyan
Write-Host "Pack:      $Pack"
Write-Host "CS:GO:     $CsgoDir"
Write-Host "Agent:     $AgentDir"
Write-Host ""

New-Item $AgentDir -ItemType Directory -Force | Out-Null

# Keep the GSLT/admin helpers current.
$setGslt = Join-Path $AgentDir "SET_GSLT.ps1"
Invoke-WebRequest "$RawBase/deploy/windows-gameserver/SET_GSLT.ps1" -OutFile $setGslt -UseBasicParsing
$setAdmin = Join-Path $AgentDir "SET_ADMIN.ps1"
Invoke-WebRequest "$RawBase/deploy/windows-gameserver/SET_ADMIN.ps1" -OutFile $setAdmin -UseBasicParsing

# Preserve the laptop's existing backend URL / Playit endpoint / game path.
$agentConfig = Join-Path $AgentDir "server_agent.json"
if (-not (Test-Path $agentConfig)) {
    throw "server_agent.json is missing from $AgentDir. This updater is for an already-configured laptop."
}

# Existing installs created before the native Accept flow used 90 seconds.
# Keep user settings, but never let the pre-join reservation die before the
# stock ready/accept/connect sequence has time to finish.
$agentConfigJson = Get-Content $agentConfig -Raw | ConvertFrom-Json
$currentAcceptTimeout = 0
if ($null -ne $agentConfigJson.accept_timeout_seconds) {
    $currentAcceptTimeout = [double]$agentConfigJson.accept_timeout_seconds
}
if ($currentAcceptTimeout -lt 300) {
    $agentConfigJson.accept_timeout_seconds = 300
    $agentConfigJson | ConvertTo-Json -Depth 20 | Set-Content $agentConfig -Encoding UTF8
    Write-Host "    Raised accept_timeout_seconds to 300 seconds." -ForegroundColor Green
}

Write-Host "[1/4] Stopping old laptop agent/server..." -ForegroundColor Yellow
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        ($_.Name -eq "python.exe" -or $_.Name -eq "py.exe") -and
        $_.CommandLine -and
        $_.CommandLine -like "*agent.py*" -and
        $_.CommandLine -like "*$AgentDir*"
    } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

$srcdsPath = Join-Path $CsgoDir "srcds.exe"
$srcdsBackup = Join-Path $AgentDir "srcds.revival.backup.exe"

# Keep one known-working dedicated-server launcher across ordinary GC updates.
# The public pack intentionally no longer contains srcds.exe.
if ((Test-Path $srcdsPath) -and -not (Test-Path $srcdsBackup)) {
    Copy-Item $srcdsPath $srcdsBackup -Force
    Write-Host "    Backed up existing revival server launcher." -ForegroundColor DarkGray
}
if (-not (Test-Path $srcdsPath) -and (Test-Path $srcdsBackup)) {
    Copy-Item $srcdsBackup $srcdsPath -Force
    Write-Host "    Restored preserved revival server launcher." -ForegroundColor Green
}
if (-not (Test-Path $srcdsPath)) {
    throw "Revival srcds.exe is missing. Windows Security may have quarantined it. Restore the previously trusted server launcher first; current packs deliberately do not ship a replacement."
}

Get-CimInstance Win32_Process -Filter "Name='srcds.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.ExecutablePath -and
        ([IO.Path]::GetFullPath($_.ExecutablePath) -eq [IO.Path]::GetFullPath($srcdsPath))
    } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

Write-Host "[2/4] Installing current GC/data runtime from pack..." -ForegroundColor Yellow
$temp = Join-Path $env:TEMP ("csgo-revival-laptop-" + [guid]::NewGuid().ToString("N"))
New-Item $temp -ItemType Directory -Force | Out-Null

try {
    Expand-Archive -Path $Pack -DestinationPath $temp -Force

    $required = @(
        "csgo_gc\\csgo_gc.dll",
        "csgo_gc\\config.txt",
        "csgo\\scripts\\items\\items_game.txt"
    )
    foreach ($rel in $required) {
        Need-Path (Join-Path $temp $rel) "Pack file $rel"
    }

    # Preserve the existing dedicated-server launcher. Only the GC DLL and
    # data/config are updated by normal revival packs.
    $gcRuntimeDir = Join-Path $CsgoDir "csgo_gc"
    New-Item $gcRuntimeDir -ItemType Directory -Force | Out-Null
    Copy-Item (Join-Path $temp "csgo_gc\\csgo_gc.dll") (Join-Path $gcRuntimeDir "csgo_gc.dll") -Force
    Copy-Item (Join-Path $temp "csgo_gc\\config.txt") (Join-Path $gcRuntimeDir "config.txt") -Force

    $wrongRootGc = Join-Path $CsgoDir "csgo_gc.dll"
    if (Test-Path $wrongRootGc) {
        Remove-Item $wrongRootGc -Force
    }

    $itemsDest = Join-Path $CsgoDir "csgo\\scripts\\items"
    New-Item $itemsDest -ItemType Directory -Force | Out-Null
    Copy-Item (Join-Path $temp "csgo\\scripts\\items\\items_game.txt") (Join-Path $itemsDest "items_game.txt") -Force
}
finally {
    Remove-Item $temp -Recurse -Force -ErrorAction SilentlyContinue
}

# Ensure the Operation Riptide Insertion II mission is actually hostable.
$insertionDest = Join-Path $CsgoDir "csgo\maps\cs_insertion2.bsp"
if (-not (Test-Path $insertionDest)) {
    $steamapps = Split-Path (Split-Path $CsgoDir -Parent) -Parent
    $workshopBase = Join-Path $steamapps "workshop\content\730"
    $workshopIds = @("2395333051", "2760936305")
    $installedInsertion = $false

    foreach ($wid in $workshopIds) {
        $itemDir = Join-Path $workshopBase $wid
        if (-not (Test-Path $itemDir)) { continue }

        $directBsp = Get-ChildItem $itemDir -Recurse -Filter "cs_insertion2.bsp" -File -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($directBsp) {
            $mapsDest = Split-Path $insertionDest -Parent
            New-Item $mapsDest -ItemType Directory -Force | Out-Null
            Get-ChildItem $directBsp.Directory.FullName -File -ErrorAction SilentlyContinue |
                Where-Object { $_.Name.ToLower().StartsWith("cs_insertion2.") } |
                ForEach-Object { Copy-Item $_.FullName (Join-Path $mapsDest $_.Name) -Force }
            $installedInsertion = Test-Path $insertionDest
            if ($installedInsertion) { break }
        }

        $legacy = Get-ChildItem $itemDir -Recurse -Filter "*legacy.bin" -File -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($legacy) {
            $sevenCandidates = @()
            $sevenCmd = Get-Command 7z.exe -ErrorAction SilentlyContinue
            if ($sevenCmd) { $sevenCandidates += $sevenCmd.Source }
            $sevenCandidates += (Join-Path $env:ProgramFiles "7-Zip\7z.exe")
            if (${env:ProgramFiles(x86)}) { $sevenCandidates += (Join-Path ${env:ProgramFiles(x86)} "7-Zip\7z.exe") }
            $seven = $sevenCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

            if ($seven) {
                $mapTemp = Join-Path $env:TEMP ("revival-insertion2-" + [guid]::NewGuid().ToString("N"))
                New-Item $mapTemp -ItemType Directory -Force | Out-Null
                try {
                    & $seven x -y "-o$mapTemp" $legacy.FullName | Out-Null
                    $bsp = Get-ChildItem $mapTemp -Recurse -Filter "cs_insertion2.bsp" -File -ErrorAction SilentlyContinue |
                        Select-Object -First 1
                    if ($bsp) {
                        New-Item (Split-Path $insertionDest -Parent) -ItemType Directory -Force | Out-Null
                        Copy-Item $bsp.FullName $insertionDest -Force
                        $installedInsertion = $true
                        break
                    }
                }
                finally {
                    Remove-Item $mapTemp -Recurse -Force -ErrorAction SilentlyContinue
                }
            }
        }
    }

    if (-not $installedInsertion) {
        Write-Host "    Insertion II not in Steam cache; downloading directly with Valve SteamCMD..." -ForegroundColor Yellow
        $steamcmdDir = Join-Path $env:LOCALAPPDATA "CSGO-Revival\steamcmd"
        $steamcmdExe = Join-Path $steamcmdDir "steamcmd.exe"
        New-Item $steamcmdDir -ItemType Directory -Force | Out-Null

        if (-not (Test-Path $steamcmdExe)) {
            $steamcmdZip = Join-Path $env:TEMP ("steamcmd-" + [guid]::NewGuid().ToString("N") + ".zip")
            try {
                Invoke-WebRequest "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip" -OutFile $steamcmdZip -UseBasicParsing
                Expand-Archive -Path $steamcmdZip -DestinationPath $steamcmdDir -Force
            }
            finally {
                Remove-Item $steamcmdZip -Force -ErrorAction SilentlyContinue
            }
        }

        foreach ($wid in $workshopIds) {
            & $steamcmdExe +login anonymous +workshop_download_item 730 $wid validate +quit
            if ($LASTEXITCODE -ne 0) { continue }

            $itemDir = Join-Path $steamcmdDir ("steamapps\workshop\content\730\" + $wid)
            if (-not (Test-Path $itemDir)) { continue }

            $directBsp = Get-ChildItem $itemDir -Recurse -Filter "cs_insertion2.bsp" -File -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($directBsp) {
                New-Item (Split-Path $insertionDest -Parent) -ItemType Directory -Force | Out-Null
                Copy-Item $directBsp.FullName $insertionDest -Force
                $installedInsertion = $true
                break
            }

            $legacy = Get-ChildItem $itemDir -Recurse -Filter "*legacy.bin" -File -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($legacy) {
                $mapTemp = Join-Path $env:TEMP ("revival-insertion2-" + [guid]::NewGuid().ToString("N"))
                New-Item $mapTemp -ItemType Directory -Force | Out-Null
                try {
                    $extracted = $false
                    try {
                        Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
                        [IO.Compression.ZipFile]::ExtractToDirectory($legacy.FullName, $mapTemp)
                        $extracted = $true
                    }
                    catch {
                        $sevenCandidates = @()
                        $sevenCmd = Get-Command 7z.exe -ErrorAction SilentlyContinue
                        if ($sevenCmd) { $sevenCandidates += $sevenCmd.Source }
                        $sevenCandidates += (Join-Path $env:ProgramFiles "7-Zip\7z.exe")
                        if (${env:ProgramFiles(x86)}) { $sevenCandidates += (Join-Path ${env:ProgramFiles(x86)} "7-Zip\7z.exe") }
                        $seven = $sevenCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

                        if (-not $seven) {
                            $toolsDir = Join-Path $env:LOCALAPPDATA "CSGO-Revival\tools"
                            New-Item $toolsDir -ItemType Directory -Force | Out-Null
                            $seven = Join-Path $toolsDir "7zr.exe"
                            if (-not (Test-Path $seven)) {
                                Invoke-WebRequest "https://github.com/ip7z/7zip/releases/download/26.03/7zr.exe" -OutFile $seven -UseBasicParsing
                            }
                        }

                        if ($seven -and (Test-Path $seven)) {
                            & $seven x -y "-o$mapTemp" $legacy.FullName | Out-Null
                            $extracted = ($LASTEXITCODE -eq 0)
                        }
                    }

                    if ($extracted) {
                        $bsp = Get-ChildItem $mapTemp -Recurse -Filter "cs_insertion2.bsp" -File -ErrorAction SilentlyContinue |
                            Select-Object -First 1
                        if ($bsp) {
                            $mapsDest = Split-Path $insertionDest -Parent
                            New-Item $mapsDest -ItemType Directory -Force | Out-Null
                            Get-ChildItem $bsp.Directory.FullName -File -ErrorAction SilentlyContinue |
                                Where-Object { $_.Name.ToLower().StartsWith("cs_insertion2.") } |
                                ForEach-Object { Copy-Item $_.FullName (Join-Path $mapsDest $_.Name) -Force }
                            $installedInsertion = Test-Path $insertionDest
                            if ($installedInsertion) { break }
                        }
                    }
                }
                finally {
                    Remove-Item $mapTemp -Recurse -Force -ErrorAction SilentlyContinue
                }
            }
        }
    }

    if ($installedInsertion) {
        Write-Host "    Installed Insertion II BSP (NAV is generated/served by SRCDS)." -ForegroundColor Green
    }
    else {
        throw "SteamCMD could not install Insertion II automatically. Check SteamCMD output above for the Workshop download error."
    }
}

Write-Host "[3/4] Updating laptop agent files..." -ForegroundColor Yellow
Invoke-WebRequest "$RawBase/deploy/windows-gameserver/agent.py" -OutFile (Join-Path $AgentDir "agent.py") -UseBasicParsing
Invoke-WebRequest "$RawBase/deploy/windows-gameserver/start-agent.bat" -OutFile (Join-Path $AgentDir "start-agent.bat") -UseBasicParsing

Write-Host "[4/5] Installing/refreshing server admin moderation runtime..." -ForegroundColor Yellow
$adminInstaller = Join-Path $AgentDir "INSTALL_ADMIN_MODERATION.ps1"
$adminPlugin = Join-Path $AgentDir "revival_admin.sp"
Invoke-WebRequest "$RawBase/deploy/windows-gameserver/INSTALL_ADMIN_MODERATION.ps1" -OutFile $adminInstaller -UseBasicParsing
Invoke-WebRequest "$RawBase/deploy/windows-gameserver/revival_admin.sp" -OutFile $adminPlugin -UseBasicParsing
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $adminInstaller -CsgoDir $CsgoDir

Write-Host "[5/5] Validating..." -ForegroundColor Yellow
foreach ($path in @(
    (Join-Path $CsgoDir "srcds.exe"),
    (Join-Path $CsgoDir "csgo_gc\\csgo_gc.dll"),
    (Join-Path $CsgoDir "csgo_gc\\config.txt"),
    (Join-Path $CsgoDir "csgo\\scripts\\items\\items_game.txt"),
    (Join-Path $AgentDir "agent.py"),
    (Join-Path $AgentDir "start-agent.bat"),
    (Join-Path $AgentDir "SET_ADMIN.ps1"),
    (Join-Path $AgentDir "INSTALL_ADMIN_MODERATION.ps1"),
    (Join-Path $AgentDir "revival_admin.sp"),
    (Join-Path $CsgoDir "addons\metamod.vdf"),
    (Join-Path $CsgoDir "addons\sourcemod\plugins\revival_admin.smx"),
    (Join-Path $CsgoDir "addons\sourcemod\configs\admins_simple.ini"),
    $agentConfig
)) {
    Need-Path $path "Required laptop file"
}

Write-Host ""
$installedGc = Join-Path $CsgoDir "csgo_gc\csgo_gc.dll"
$installedGcText = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($installedGc))
foreach ($marker in @(
    "REVIVAL_MM_BRIDGE_CLEAN_V1",
    "REVIVAL_SERVER_RESERVATION_RETRY_V4",
    "REVIVAL_SERVER_GC_OFFLINE_DELIVERY_V1",
    "REVIVAL_SERVER_ID_EXPORT_V1",
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
    "REVIVAL_RANDOMIZED_LEGACY_DROPS_V2",
    "REVIVAL_REPEATABLE_MISSIONS_V5",
    "REVIVAL_PVP_MISSION_STATS_V1",
    "REVIVAL_CUSTOM_PVP_MISSIONS_V1",
    "REVIVAL_OPERATION_REPLAY_V1",
    "REVIVAL_NATIVE_ACTIVE_QUEST_V1",
    "REVIVAL_OPERATION_SELECTION_BRIDGE_V2",
    "REVIVAL_OPERATION_SCHEMA_V2",
    "REVIVAL_OPERATION_CARD_PARSE_V3",
    "REVIVAL_OPERATION_PROGRESS_CACHE_V1",
    "REVIVAL_LIVE_OPERATION_ROUNDS_V1",
    "REVIVAL_LIVE_OPERATION_FINAL_V1",
    "REVIVAL_OPERATION_END_AUTHORITY_V1",
    "REVIVAL_LIVE_OPERATION_NO_REASSERT_V1",
    "REVIVAL_OPERATION_COMPLETION_PERSIST_V1",
    "REVIVAL_STORAGE_UNITS_V1",
    "REVIVAL_EARNED_DROPS_ONLY_V1",
    "REVIVAL_KEYLESS_CASES_V1",
    "REVIVAL_OPERATION_SUMMARY_REPAIR_V1",
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
    "REVIVAL_SERVER_UNBOX_CHAT_RELAY_V1",
    "REVIVAL_JOIN_IN_PROGRESS_G_V1",
    "REVIVAL_Q_SLOT_PAD_V1"
)) {
    if (-not $installedGcText.Contains($marker)) {
        throw "Installed laptop csgo_gc.dll is stale; missing marker $marker"
    }
}
Write-Host "    Verified current matchmaking DLL markers on laptop." -ForegroundColor Green

$agentText = Get-Content (Join-Path $AgentDir "agent.py") -Raw
if (-not $agentText.Contains("REVIVAL_AGENT_PUBLIC_RELEASE_V53")) {
    throw "Downloaded laptop agent is stale; missing REVIVAL_AGENT_PUBLIC_RELEASE_V53"
}
if (-not $agentText.Contains("REVIVAL_TEAMKILL_RULES_V1")) {
    throw "Downloaded laptop agent is missing Competitive teamkill punishment."
}
if (-not $agentText.Contains("REVIVAL_ADMIN_RESET_V1")) {
    throw "Downloaded laptop agent is missing admin major-reset handling."
}
if (-not $agentText.Contains("sv_allowdownload 1")) {
    throw "Downloaded laptop agent is missing Insertion II NAV download support."
}
if (-not $agentText.Contains("drop-in player(s) staged for live match")) {
    throw "Downloaded laptop agent is missing native-ack live late-join reservation handling."
}
if (-not $agentText.Contains("REVIVAL_SERVER_UNBOX_CHAT_RELAY_V1 queued")) {
    throw "Downloaded laptop agent is missing native unbox chat relay handling."
}
if (-not $agentText.Contains("REVIVAL_LATEJOIN_PENDING_ROSTER_V1 reservation refreshed")) {
    throw "Downloaded laptop agent is missing exact engine ready-up roster reporting."
}
if (-not $agentText.Contains("REVIVAL_JOIN_IN_PROGRESS_G_V1 match")) {
    throw "Downloaded laptop agent is missing Q-to-G live-match reservation switching."
}
if (-not $agentText.Contains("REVIVAL_Q_SLOT_PAD_V1 tournament extra-slot mode active")) {
    throw "Downloaded laptop agent is missing ten-human-slot queued reservation support."
}
if (-not $agentText.Contains("REVIVAL_NATIVE_VAC_BAN_V1 persisted")) {
    throw "Downloaded laptop agent is missing global VAC-ban enforcement."
}
if (-not $agentText.Contains("REVIVAL_GSLT_HOT_RELOAD_V1")) {
    throw "Downloaded laptop agent is missing per-match GSLT hot reload."
}
if (-not $agentText.Contains("REVIVAL_PVP_MISSION_STATS_V1") -or -not $agentText.Contains("player_kill_stats")) {
    throw "Downloaded laptop agent is missing Riptide PvP mission kill-stat tracking."
}
if (-not $agentText.Contains('"-tournament", "revival"')) {
    throw "Downloaded laptop agent is missing Source tournament slot-padding mode."
}
if (-not $agentText.Contains('"-tournament_extra_casters_slots", "10"')) {
    throw "Downloaded laptop agent is missing ten extra queued reservation slots."
}
Write-Host "    Verified current V52 public-release laptop agent (MR8 + teamkill + admin-reset + map-download + native-ack live late-join handling)." -ForegroundColor Green

$agentText = Get-Content (Join-Path $AgentDir "agent.py") -Raw
if (-not $agentText.Contains("REVIVAL_AGENT_PUBLIC_RELEASE_V53")) {
    throw "Downloaded laptop agent is stale; missing REVIVAL_AGENT_PUBLIC_RELEASE_V53"
}
Write-Host "    Verified V52 laptop agent + admin moderation runtime." -ForegroundColor Green

Write-Host "REVIVAL_SERVER_LAUNCHER_PRESERVE_V1: existing srcds.exe preserved." -ForegroundColor DarkGray
Write-Host "LAPTOP UPDATE COMPLETE" -ForegroundColor Green
Write-Host "Your existing server_agent.json and Playit configuration were preserved."
Write-Host ""
Write-Host "Start Playit if it is not already running, then run:"
Write-Host ("  cd `"" + $AgentDir + "`"")
Write-Host "  .\\start-agent.bat"
Write-Host ""
