param(
    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
    [string]$Python = "C:\Users\Jayron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)
$ErrorActionPreference = 'Stop'
& $Python -X utf8 (Join-Path $ProjectRoot 'tools/run_manual_close_cycle.py')
if ($LASTEXITCODE -ne 0) { throw "manual close cycle failed: $LASTEXITCODE" }
