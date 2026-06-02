$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -Path $ProjectRoot
.\ProPrintPreflightAgent.exe
