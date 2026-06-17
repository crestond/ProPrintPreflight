$Version = "1.1.4"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -Path $ProjectRoot

$ReleaseDir = Join-Path $ProjectRoot "release"
$ZipPath = Join-Path $ReleaseDir "ProPrintPreflight-v$Version.zip"
$StagingDir = Join-Path $ReleaseDir "package_staging"

$RequiredFiles = @(
    "ProPrintPreflightAgent.exe",
    "ProPrintPreflightWeb.exe",
    "config.json",
    "README.md",
    "assets\PreflightIcon.ico",
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

if (Test-Path -Path $StagingDir) {
    Remove-Item -Recurse -Force -Path $StagingDir
}

New-Item -ItemType Directory -Force -Path $StagingDir | Out-Null

foreach ($File in $RequiredFiles) {
    $Source = Join-Path $ProjectRoot $File
    $Destination = Join-Path $StagingDir $File
    $DestinationDir = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $DestinationDir | Out-Null
    Copy-Item -Force -Path $Source -Destination $Destination
}

Compress-Archive -Force `
    -Path (Join-Path $StagingDir "*") `
    -DestinationPath $ZipPath

Remove-Item -Recurse -Force -Path $StagingDir

Write-Host "Release package created: $ZipPath"
