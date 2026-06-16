$Version = "1.1.3"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -Path $ProjectRoot

$ReleaseDir = Join-Path $ProjectRoot "release"
$ZipPath = Join-Path $ReleaseDir "ProPrintPreflight-v$Version.zip"

$RequiredFiles = @(
    "ProPrintPreflightAgent.exe",
    "ProPrintPreflightWeb.exe",
    "config.json",
    "README.md",
    "scripts\run_agent.ps1",
    "scripts\run_web_exe_server.ps1"
)

$MissingFiles = @()
foreach ($File in $RequiredFiles) {
    if (-not (Test-Path -Path (Join-Path $ProjectRoot $File))) {
        $MissingFiles += $File
    }
}

if ($MissingFiles.Count -gt 0) {
    Write-Error "Cannot create release package. Missing required file(s): $($MissingFiles -join ', ')"
    exit 1
}

New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

Compress-Archive -Force `
    -Path $RequiredFiles `
    -DestinationPath $ZipPath

Write-Host "Release package created: $ZipPath"
