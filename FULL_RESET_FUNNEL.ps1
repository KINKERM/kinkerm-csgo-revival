param(
    [string]$Repo = "$env:USERPROFILE\Documents\kinkerm-csgo-revival-pinned",
    [int]$Port = 8787
)

$ErrorActionPreference = "Stop"

function Need-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run PowerShell as Administrator, then run this script again."
    }
}

function Find-Tailscale {
    $cmd = Get-Command tailscale.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidate = "$env:ProgramFiles\Tailscale\tailscale.exe"
    if (Test-Path $candidate) { return $candidate }
    throw "tailscale.exe was not found."
}

function Wait-HttpOk {
    param([string]$Url, [int]$Attempts = 30, [int]$DelaySeconds = 1)
    for ($i = 1; $i -le $Attempts; $i++) {
        try {
            $body = & curl.exe --fail --silent --show-error --max-time 8 $Url 2>$null
            if ($LASTEXITCODE -eq 0 -and (($body -join [Environment]::NewLine).Trim() -eq "ok")) {
                return $true
            }
        } catch {}
        Start-Sleep -Seconds $DelaySeconds
    }
    return $false
}

Need-Admin

$serverDir = Join-Path $Repo "server"
$serverPy = Join-Path $serverDir "revival_server.py"
if (-not (Test-Path $serverPy)) { throw "Revival backend not found: $serverPy" }

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) { throw "python.exe was not found on PATH." }

$tailscale = Find-Tailscale

Write-Host ""
Write-Host "=== FULL REVIVAL BACKEND + TAILSCALE FUNNEL RESET ===" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/7] Stopping stale revival backend..." -ForegroundColor Yellow
$owners = @()
try {
    $owners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
} catch {}

foreach ($pidValue in $owners) {
    if ($pidValue -and $pidValue -ne $PID) {
        Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
    }
}

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    ($_.Name -eq "python.exe" -or $_.Name -eq "python3.exe" -or $_.Name -eq "py.exe") -and
    $_.CommandLine -and $_.CommandLine -like "*revival_server.py*"
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Milliseconds 500

Write-Host "[2/7] Clearing Funnel configuration..." -ForegroundColor Yellow
& $tailscale funnel reset | Out-Host
if ($LASTEXITCODE -ne 0) { throw "tailscale funnel reset failed with exit code $LASTEXITCODE." }

Write-Host "[3/7] Restarting Tailscale service..." -ForegroundColor Yellow
$svc = Get-Service -Name "Tailscale" -ErrorAction SilentlyContinue
if (-not $svc) { throw "Windows Tailscale service was not found." }
Restart-Service -Name "Tailscale" -Force
$svc.WaitForStatus([System.ServiceProcess.ServiceControllerStatus]::Running, [TimeSpan]::FromSeconds(20))
Start-Sleep -Seconds 3

$online = $false
for ($i = 0; $i -lt 20; $i++) {
    try {
        $statusText = (& $tailscale status 2>&1) -join [Environment]::NewLine
        if ($LASTEXITCODE -eq 0 -and $statusText -notmatch "Stopped|NeedsLogin|Logged out") {
            $online = $true
            break
        }
    } catch {}
    Start-Sleep -Seconds 1
}
if (-not $online) { throw "Tailscale did not come back online. Open Tailscale and make sure this PC is signed in." }

Write-Host "[4/7] Flushing Windows DNS cache..." -ForegroundColor Yellow
ipconfig /flushdns | Out-Null

Write-Host "[5/7] Starting revival backend on 0.0.0.0:$Port..." -ForegroundColor Yellow
$stdout = Join-Path $serverDir "reset-backend.stdout.log"
$stderr = Join-Path $serverDir "reset-backend.stderr.log"
Remove-Item $stdout,$stderr -Force -ErrorAction SilentlyContinue

$startArgs = @("revival_server.py", "--host", "0.0.0.0", "--port", "$Port")
Start-Process -FilePath $python.Source -ArgumentList $startArgs -WorkingDirectory $serverDir -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Minimized | Out-Null

$localUrl = "http://127.0.0.1:$Port/health"
if (-not (Wait-HttpOk -Url $localUrl -Attempts 30 -DelaySeconds 1)) {
    Write-Host ""
    Write-Host "Backend stderr:" -ForegroundColor Red
    if (Test-Path $stderr) { Get-Content $stderr -Tail 40 | Out-Host }
    throw "Local backend health check failed: $localUrl"
}
Write-Host "    Local backend: OK" -ForegroundColor Green

Write-Host "[6/7] Recreating Funnel..." -ForegroundColor Yellow
& $tailscale funnel --bg --yes $Port | Out-Host
if ($LASTEXITCODE -ne 0) { throw "tailscale funnel --bg --yes $Port failed with exit code $LASTEXITCODE." }

Start-Sleep -Seconds 2

$statusJsonText = (& $tailscale status --json 2>$null) -join [Environment]::NewLine
if ($LASTEXITCODE -ne 0 -or -not $statusJsonText.Trim()) { throw "Could not read Tailscale status JSON." }
$statusJson = $statusJsonText | ConvertFrom-Json
$dnsName = [string]$statusJson.Self.DNSName
$dnsName = $dnsName.Trim().TrimEnd(".")
if (-not $dnsName) { throw "Tailscale did not report this PC's DNS name." }
$publicUrl = "https://$dnsName/health"

Write-Host "[7/7] Testing public Funnel with curl..." -ForegroundColor Yellow
if (-not (Wait-HttpOk -Url $publicUrl -Attempts 20 -DelaySeconds 2)) {
    Write-Host ""
    & $tailscale funnel status | Out-Host
    throw "Public Funnel health check still failed: $publicUrl"
}

Write-Host ""
Write-Host "FULL RESET COMPLETE" -ForegroundColor Green
Write-Host "Local:  $localUrl" -ForegroundColor Green
Write-Host "Public: $publicUrl" -ForegroundColor Green
Write-Host ""
Write-Host "Now restart the LAPTOP agent so it reconnects cleanly:" -ForegroundColor Cyan
Write-Host "  cd C:\CSGO-Revival-Agent"
Write-Host "  .\start-agent.bat"
