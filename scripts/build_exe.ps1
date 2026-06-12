$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -Path $ProjectRoot

.\.venv\Scripts\python.exe -m py_compile .\pro_print_preflight_agent.py .\pro_print_preflight_web_entry.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

.\.venv\Scripts\pytest.exe --basetemp .\.pytest_tmp_build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

.\.venv\Scripts\pyinstaller.exe --onefile --name ProPrintPreflightAgent --icon .\assets\PreflightIcon.ico .\pro_print_preflight_agent.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

.\.venv\Scripts\pyinstaller.exe --onefile --name ProPrintPreflightWeb .\pro_print_preflight_web_entry.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Copy-Item .\dist\ProPrintPreflightAgent.exe .\ProPrintPreflightAgent.exe -Force
Copy-Item .\dist\ProPrintPreflightWeb.exe .\ProPrintPreflightWeb.exe -Force

Write-Host "Build complete: ProPrintPreflightAgent.exe, and ProPrintPreflightWeb.exe"
