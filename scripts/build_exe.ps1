$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -Path $ProjectRoot

.\.venv\Scripts\python.exe -m py_compile .\pro_print_preflight_agent.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

.\.venv\Scripts\pytest.exe --basetemp .\.pytest_tmp_build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

.\.venv\Scripts\pyinstaller.exe --onefile --name ProPrintPreflightAgent .\pro_print_preflight_agent.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Copy-Item .\dist\ProPrintPreflightAgent.exe .\ProPrintPreflightAgent.exe -Force

Write-Host "Build complete: ProPrintPreflightAgent.exe"
