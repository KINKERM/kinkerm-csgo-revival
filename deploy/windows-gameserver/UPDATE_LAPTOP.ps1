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

# Preserve the laptop's existing backend URL / Playit endpoint / game path.
$agentConfig = Join-Path $AgentDir "server_agent.json"
if (-not (Test-Path $agentConfig)) {
    throw "server_agent.json is missing from $AgentDir. This updater is for an already-configured laptop."
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
Get-CimInstance Win32_Process -Filter "Name='srcds.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.ExecutablePath -and
        ([IO.Path]::GetFullPath($_.ExecutablePath) -eq [IO.Path]::GetFullPath($srcdsPath))
    } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

Write-Host "[2/4] Installing current server runtime from pack..." -ForegroundColor Yellow
$temp = Join-Path $env:TEMP ("csgo-revival-laptop-" + [guid]::NewGuid().ToString("N"))
New-Item $temp -ItemType Directory -Force | Out-Null

try {
    Expand-Archive -Path $Pack -DestinationPath $temp -Force

    $required = @(
        "srcds.exe",
        "csgo_gc.dll",
        "csgo_gc\\config.txt",
        "csgo\\scripts\\items\\items_game.txt"
    )
    foreach ($rel in $required) {
        Need-Path (Join-Path $temp $rel) "Pack file $rel"
    }

    Copy-Item (Join-Path $temp "srcds.exe") (Join-Path $CsgoDir "srcds.exe") -Force
    Copy-Item (Join-Path $temp "csgo_gc.dll") (Join-Path $CsgoDir "csgo_gc.dll") -Force

    New-Item (Join-Path $CsgoDir "csgo_gc") -ItemType Directory -Force | Out-Null
    Copy-Item (Join-Path $temp "csgo_gc\\config.txt") (Join-Path $CsgoDir "csgo_gc\\config.txt") -Force

    $itemsDest = Join-Path $CsgoDir "csgo\\scripts\\items"
    New-Item $itemsDest -ItemType Directory -Force | Out-Null
    Copy-Item (Join-Path $temp "csgo\\scripts\\items\\items_game.txt") (Join-Path $itemsDest "items_game.txt") -Force
}
finally {
    Remove-Item $temp -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "[3/4] Updating laptop agent files..." -ForegroundColor Yellow
Invoke-WebRequest "$RawBase/deploy/windows-gameserver/agent.py" -OutFile (Join-Path $AgentDir "agent.py") -UseBasicParsing
Invoke-WebRequest "$RawBase/deploy/windows-gameserver/start-agent.bat" -OutFile (Join-Path $AgentDir "start-agent.bat") -UseBasicParsing

Write-Host "[4/4] Validating..." -ForegroundColor Yellow
foreach ($path in @(
    (Join-Path $CsgoDir "srcds.exe"),
    (Join-Path $CsgoDir "csgo_gc\\csgo_gc.dll"),
    (Join-Path $CsgoDir "csgo_gc\\config.txt"),
    (Join-Path $CsgoDir "csgo\\scripts\\items\\items_game.txt"),
    (Join-Path $AgentDir "agent.py"),
    (Join-Path $AgentDir "start-agent.bat"),
    $agentConfig
)) {
    Need-Path $path "Required laptop file"
}

Write-Host ""
Write-Host "LAPTOP UPDATE COMPLETE" -ForegroundColor Green
Write-Host "Your existing server_agent.json and Playit configuration were preserved."
Write-Host ""
Write-Host "Start Playit if it is not already running, then run:"
Write-Host ("  cd `"" + $AgentDir + "`"")
Write-Host "  .\\start-agent.bat"
Write-Host ""
