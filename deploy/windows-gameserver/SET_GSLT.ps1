param(
    [string]$AgentDir = "C:\\CSGO-Revival-Agent",
    [string]$CsgoDir = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\csgo legacy"
)

$ErrorActionPreference = "Stop"

function Read-PlainTextSecureString {
    param([Security.SecureString]$Secure)
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

$configPath = Join-Path $AgentDir "server_agent.json"
if (-not (Test-Path $configPath)) {
    throw "server_agent.json not found: $configPath"
}

Write-Host ""
Write-Host "=== CS:GO Revival GSLT replacement ===" -ForegroundColor Cyan
Write-Host "This updates steam_account_token without printing the token." -ForegroundColor DarkGray
Write-Host ""

$secure = Read-Host "Paste the NEW GSLT" -AsSecureString
$token = Read-PlainTextSecureString $secure
$token = [string]$token
$token = $token.Trim()

if (-not $token) {
    throw "Token was empty."
}
if ($token -match '[\\s"]') {
    throw "Token contains whitespace or a quote; paste only the GSLT itself."
}
if ($token.Length -lt 20 -or $token.Length -gt 128) {
    throw "Token length looks invalid ($($token.Length) characters)."
}

$raw = Get-Content $configPath -Raw
$config = $raw | ConvertFrom-Json

if ($null -eq $config.PSObject.Properties["steam_account_token"]) {
    $config | Add-Member -NotePropertyName "steam_account_token" -NotePropertyValue $token
}
else {
    $config.steam_account_token = $token
}

$backup = "$configPath.bak"
Copy-Item $configPath $backup -Force

$temp = "$configPath.tmp"
$config | ConvertTo-Json -Depth 20 | Set-Content $temp -Encoding UTF8
Move-Item $temp $configPath -Force

# Also update the already-generated early cfg. agent.py rewrites this again
# from server_agent.json before every future match.
$earlyCfg = Join-Path $CsgoDir "csgo\\cfg\\revival_competitive.cfg"
if (Test-Path $earlyCfg) {
    $lines = @(Get-Content $earlyCfg)
    $found = $false
    $newLines = foreach ($line in $lines) {
        if ($line -match '^\\s*sv_setsteamaccount(?:\\s|$)') {
            $found = $true
            'sv_setsteamaccount "' + $token + '"'
        }
        else {
            $line
        }
    }
    if (-not $found) {
        $newLines += 'sv_setsteamaccount "' + $token + '"'
    }
    $newLines | Set-Content $earlyCfg -Encoding ASCII
}

# Do not print the secret. Show only a tiny fingerprint so the user can tell
# that a different value was written.
$fingerprint = if ($token.Length -ge 4) { $token.Substring($token.Length - 4) } else { "****" }
Write-Host ""
Write-Host "GSLT updated successfully (ends in ...$fingerprint)." -ForegroundColor Green
Write-Host "Backup: $backup" -ForegroundColor DarkGray
Write-Host ""
$agentPy = Join-Path $AgentDir "agent.py"
$hotReload = $false
if (Test-Path $agentPy) {
    $agentText = Get-Content $agentPy -Raw
    $hotReload = $agentText.Contains("REVIVAL_GSLT_HOT_RELOAD_V1")
}

if ($hotReload) {
    Write-Host "The current revival agent reloads steam_account_token before each NEW srcds match." -ForegroundColor Green
    Write-Host "If a match is running now, leave it alone; the new token is used on the next server start." -ForegroundColor Yellow
}
else {
    Write-Host "This laptop has an older agent that does not hot-reload server_agent.json." -ForegroundColor Yellow
    Write-Host "After the current match ends, close/restart the agent with start-agent.bat so it loads the new token." -ForegroundColor Yellow
}
