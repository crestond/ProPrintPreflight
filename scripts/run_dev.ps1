$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -Path $ProjectRoot
.\.venv\Scripts\python.exe .\pro_print_preflight_agent.py
