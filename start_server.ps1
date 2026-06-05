param(
    [int]$Port = 8000,
    [switch]$SeedDemo,
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"

# Some Codex/Windows shells expose both Path and PATH. PowerShell Start-Process
# treats environment keys case-insensitively and fails unless the duplicate is removed.
[Environment]::SetEnvironmentVariable("PATH", $null, "Process")

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root

$netstat = Join-Path $env:SystemRoot "System32\netstat.exe"
$python = "C:\Users\zj\AppData\Local\Programs\Python\Python312\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command python).Source
}

$oldPids = & $netstat -ano |
    Select-String ":$Port\s+.*LISTENING\s+(\d+)" |
    ForEach-Object { [regex]::Match($_.Line, "LISTENING\s+(\d+)").Groups[1].Value } |
    Select-Object -Unique

foreach ($pidText in $oldPids) {
    if ($pidText) {
        Stop-Process -Id ([int]$pidText) -Force -ErrorAction SilentlyContinue
    }
}

Write-Output "Repairing index metadata..."
& $python -m backend.app.repair_index_metadata

if ($SeedDemo) {
    & $python -m backend.app.seed_demo
}

$uvicornArgs = @("-m", "uvicorn", "backend.app.web.main:app", "--host", "127.0.0.1", "--port", "$Port")

if ($Foreground) {
    Write-Output "SEU Search server starting: http://127.0.0.1:$Port/"
    Write-Output "Press Ctrl+C to stop the server."
    & $python @uvicornArgs
    exit $LASTEXITCODE
}

$logDir = Join-Path $root "backend\data"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$out = Join-Path $logDir "server.log"
$err = Join-Path $logDir "server.err.log"

$proc = Start-Process `
    -FilePath $python `
    -ArgumentList $uvicornArgs `
    -WorkingDirectory $root `
    -RedirectStandardOutput $out `
    -RedirectStandardError $err `
    -WindowStyle Hidden `
    -PassThru

$health = $null
for ($i = 0; $i -lt 20; $i++) {
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -TimeoutSec 3
        break
    }
    catch {
        Start-Sleep -Milliseconds 500
    }
}

if (-not $health) {
    Write-Output "Server did not pass health check. Last stderr lines:"
    if (Test-Path -LiteralPath $err) {
        Get-Content -LiteralPath $err -Tail 40
    }
    throw "Failed to start server on http://127.0.0.1:$Port/"
}

Write-Output "SEU Search server started: http://127.0.0.1:$Port/"
Write-Output "PID: $($proc.Id)"
Write-Output "Documents: $($health.documents), chunks: $($health.chunks)"
