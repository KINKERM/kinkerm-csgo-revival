param(
    [Parameter(Mandatory=$true)][string]$RevivalRepo,
    [Parameter(Mandatory=$true)][string]$CsgoDir
)

$ErrorActionPreference = "Stop"

function Need-Path([string]$Path, [string]$Label) {
    if (-not (Test-Path $Path)) { throw "$Label not found: $Path" }
}

$panoramaDir = Join-Path $CsgoDir "csgo\panorama"
$repoPbinTool = Join-Path $RevivalRepo "tools\pbin.py"
$gamePbinTool = Join-Path $panoramaDir "pbin.py"
$codePbin = Join-Path $panoramaDir "code.pbin"
$originalPbin = Join-Path $panoramaDir "_code.pbin"
$tableFile = Join-Path $panoramaDir "code.pbin.table"
$stageDir = Join-Path $panoramaDir "panorama"
$panoramaDll = Join-Path $CsgoDir "bin\panorama.dll"

# Undo the incorrect custom SearchPaths workaround from earlier revisions.
$gameInfo = Join-Path $CsgoDir "csgo\gameinfo.txt"
if (Test-Path $gameInfo) {
    $gameInfoText = [IO.File]::ReadAllText($gameInfo)
    $cleaned = [Text.RegularExpressions.Regex]::Replace(
        $gameInfoText,
        "(?im)^\s*Game(?:\+Mod)?\s+[^\r\n]*custom[\\/]kinkerm_revival[^\r\n]*\r?\n?",
        ""
    )
    if ($cleaned -ne $gameInfoText) {
        [IO.File]::WriteAllText($gameInfo, $cleaned, [Text.UTF8Encoding]::new($false))
        Write-Host "[pbin] removed obsolete custom/kinkerm_revival SearchPaths line" -ForegroundColor Yellow
    }
}
$badCustom = Join-Path $CsgoDir "csgo\custom\kinkerm_revival"
if (Test-Path $badCustom) { Remove-Item $badCustom -Recurse -Force }

Need-Path $panoramaDir "CS:GO Panorama directory"
Need-Path $repoPbinTool "Revival pbin.py"
Need-Path $codePbin "CS:GO code.pbin"
Need-Path $panoramaDll "CS:GO panorama.dll"

Copy-Item $repoPbinTool $gamePbinTool -Force

# Preserve the baseline once. Existing revivals normally already have _code.pbin.
if (-not (Test-Path $originalPbin)) {
    Copy-Item $codePbin $originalPbin -Force
    Write-Host "[pbin] saved original code.pbin as _code.pbin" -ForegroundColor Green
}

# Always unpack a clean baseline so stale staging files cannot leak into the new PBIN.
if (Test-Path $stageDir) { Remove-Item $stageDir -Recurse -Force }
if (Test-Path $tableFile) { Remove-Item $tableFile -Force }

Push-Location $panoramaDir
try {
    & py -3 ".\pbin.py" unpack "_code.pbin"
    if ($LASTEXITCODE -ne 0) {
        throw "pbin.py unpack failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

Need-Path $stageDir "Unpacked Panorama staging directory"

# Overlay the revival Panorama tree onto the unpacked PBIN tree.
Copy-Item -Path (Join-Path $RevivalRepo "panorama\*") -Destination $stageDir -Recurse -Force

# code.pbin has fixed per-file slots. Compact the three matchmaking files changed
# by this branch so they stay inside the original PBIN slot sizes.
$stageXml = Join-Path $stageDir "layout\mainmenu_play.xml"
$stageJs  = Join-Path $stageDir "scripts\mainmenu_play.js"
$stageCss = Join-Path $stageDir "styles\mainmenu_play.css"
Need-Path $stageXml "Staged mainmenu_play.xml"
Need-Path $stageJs "Staged mainmenu_play.js"
Need-Path $stageCss "Staged mainmenu_play.css"

$xmlText = [IO.File]::ReadAllText($stageXml)
$xmlText = [Text.RegularExpressions.Regex]::Replace($xmlText, ">\s+<", "><")
[IO.File]::WriteAllText($stageXml, $xmlText, [Text.UTF8Encoding]::new($false))

foreach ($compactFile in @($stageJs, $stageCss)) {
    $raw = [IO.File]::ReadAllText($compactFile)
    $compactLines = [Text.RegularExpressions.Regex]::Split($raw, "\r?\n") |
        ForEach-Object { $_.TrimEnd() } |
        Where-Object { $_.Trim().Length -gt 0 }
    [IO.File]::WriteAllText(
        $compactFile,
        ($compactLines -join "`n"),
        [Text.UTF8Encoding]::new($false)
    )
}

Push-Location $panoramaDir
try {
    & py -3 ".\pbin.py" pack
    if ($LASTEXITCODE -ne 0) {
        throw "pbin.py pack failed with exit code $LASTEXITCODE"
    }

    & py -3 ".\pbin.py" patch_panorama
    if ($LASTEXITCODE -ne 0) {
        throw "pbin.py patch_panorama failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

# Validate the actual packed archive, not merely the loose source files.
# PBIN entries are stored uncompressed/fixed-slot, so the UTF-8 source markers
# must be present literally in code.pbin. Use native PowerShell here to avoid
# shell quoting problems with Python -c on Windows.
$packedBytes = [IO.File]::ReadAllBytes($codePbin)
$packedText = [Text.Encoding]::UTF8.GetString($packedBytes)
if (-not $packedText.Contains("mg_revival_pool")) {
    throw "Packed code.pbin is missing the stock Revival mapgroup marker"
}
if (-not $packedText.Contains("_GetRevivalValidationMapGroup")) {
    throw "Packed code.pbin is missing the Revival queue validation logic"
}
Write-Host "PBIN queue markers OK" -ForegroundColor Green

$newHash = (Get-FileHash $codePbin -Algorithm SHA256).Hash
$oldHash = (Get-FileHash $originalPbin -Algorithm SHA256).Hash
if ($newHash -eq $oldHash) {
    throw "PBIN validation failed: rebuilt code.pbin is identical to _code.pbin"
}

Push-Location $panoramaDir
try {
    & py -3 ".\pbin.py" patch_panorama
    if ($LASTEXITCODE -ne 0) { throw "panorama.dll patch validation failed" }
}
finally {
    Pop-Location
}

Write-Host "[pbin] code.pbin rebuilt and panorama.dll patch verified." -ForegroundColor Green
