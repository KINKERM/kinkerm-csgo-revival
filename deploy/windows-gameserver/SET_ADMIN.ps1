param(
    [Parameter(Mandatory=$true)]
    [string]$SteamId64
)
$ErrorActionPreference = "Stop"
if ($SteamId64 -notmatch '^[0-9]{17}$') {
    throw "SteamID64 must be 17 digits."
}
$path = Join-Path $PSScriptRoot "server_agent.json"
if (-not (Test-Path $path)) {
    throw "server_agent.json not found. Copy server_agent.example.json first."
}
$data = Get-Content $path -Raw | ConvertFrom-Json
$data.admin_steamids = @($SteamId64)
$data | ConvertTo-Json -Depth 8 | Set-Content $path -Encoding UTF8
Write-Host "Server admin configured: $SteamId64"
Write-Host "Restart start-agent.bat after this change."
