# ProPrintPreflight

Windows PDF preflight agent for print-production workflows.

The agent watches `Preflight_System\Incoming`, validates incoming files, runs PDF preflight checks, moves finished jobs to `Passed`, `Needs_Fix`, or `Rejected`, and writes reports/logs.

## Release Package

Each release zip should include only the files needed to run the agent on a Windows server:

```text
ProPrintPreflightAgent.exe
config.json
README.md
```

Do not include development or runtime folders in the release zip:

```text
.venv\
build\
dist\
tests\
__pycache__\
.pytest_cache\
.pytest_tmp*
Preflight_System\
pro_print_runtime.log
*.spec
```

`requirements.txt` is not required in the release zip when deploying the packaged `.exe`. It is only needed for development from source.

## Server Layout

Recommended server folder:

```text
C:\ProPrintPreflight\
  ProPrintPreflightAgent.exe
  pro_print_preflight_agent.py
  pro_print_internal_web.py
  config.json
  README.md
  requirements.txt
  scripts\
  Preflight_System\
    Incoming\
    Passed\
    Needs_Fix\
    Rejected\
    Reports\
    Logs\
    Metadata\
    Upload_Staging\
```

The app will create missing `Preflight_System` folders at startup, but creating them manually makes the deployment easier to inspect.

## Running Manually

From PowerShell on the server:

```powershell
cd C:\ProPrintPreflight
.\ProPrintPreflightAgent.exe
```

Leave the PowerShell window open while the agent is running. Press `Ctrl+C` in that same window to stop only this agent.

## Expected Workflow

```text
Incoming   - users or upload tools place PDFs here
Passed     - print-ready PDFs are moved here
Needs_Fix  - valid PDFs that fail preflight rules are moved here
Rejected   - non-PDF, empty, malformed, or unprocessable files are moved here
Reports    - generated PDF reports
Logs       - CSV processing log
```

Runtime logs are written to:

```text
pro_print_runtime.log
```

## Development Commands

Run from source:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_dev.ps1
```

Run the packaged executable:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_agent.ps1
```

Run the internal upload/status UI from source:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_web.ps1
```

The internal UI opens at `http://127.0.0.1:8080/`. Keep the preflight agent running in a separate PowerShell window; the UI uploads PDFs into `Preflight_System\Incoming`, and the agent performs the actual PDF processing.

Run the internal upload/status UI for LAN testing from a server or VM:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_web_server.ps1
```

This listens on `0.0.0.0:8080`, so other computers on the same network can reach the UI at `http://SERVER-IP:8080/`. Keep this internal-only unless IIS, HTTPS, and authentication are added.

Allow inbound access to the direct test UI on the server/VM:

```powershell
New-NetFirewallRule -DisplayName "Pro Print Internal UI 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow
```

Build/update the executable:

```powershell
.\build_exe.bat
```

The build script runs a syntax check, runs tests, builds with PyInstaller, and replaces the root `ProPrintPreflightAgent.exe` with the new build.

## VM / Server Smoke Test

For a local Windows Server VM or internal pilot, run two PowerShell windows from `C:\ProPrintPreflight`.

Worker:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_dev.ps1
```

LAN web UI:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_web_server.ps1
```

Then browse from another workstation to:

```text
http://SERVER-IP:8080/
```

Smoke test with a small PDF and confirm:

- the job appears in the UI
- status updates after processing
- trim/bleed values display
- the report link opens
- metadata is written under `Preflight_System\Metadata`
- processed source PDFs are retained or deleted according to `config.json`
