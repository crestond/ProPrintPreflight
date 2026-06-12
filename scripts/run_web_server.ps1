$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -Path $ProjectRoot

.\.venv\Scripts\python.exe -c "import pro_print_internal_web as w; w.run_server(host='0.0.0.0', port=8080)"