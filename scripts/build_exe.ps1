$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -Path $ProjectRoot
$BuildTestTemp = ".\.pytest_tmp_build_$([System.Guid]::NewGuid().ToString('N'))"

.\.venv\Scripts\python.exe -m py_compile .\pro_print_preflight_agent.py .\pro_print_internal_web.py .\pro_print_preflight_web_entry.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

.\.venv\Scripts\python.exe -m pytest --basetemp $BuildTestTemp
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

.\.venv\Scripts\python.exe -m PyInstaller --onefile --name ProPrintPreflightAgent --icon .\assets\PreflightIcon.ico .\pro_print_preflight_agent.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

.\.venv\Scripts\python.exe -m PyInstaller --onefile --name ProPrintPreflightWeb --icon .\assets\PreflightIcon.ico .\pro_print_preflight_web_entry.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Copy-Item .\dist\ProPrintPreflightAgent.exe .\ProPrintPreflightAgent.exe -Force
Copy-Item .\dist\ProPrintPreflightWeb.exe .\ProPrintPreflightWeb.exe -Force

Write-Host "Build complete: ProPrintPreflightAgent.exe, and ProPrintPreflightWeb.exe"
