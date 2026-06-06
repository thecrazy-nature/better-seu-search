param(
    [int]$Port = 8000,
    [switch]$SeedDemo,
    [switch]$Foreground,
    [ValidateSet("hash", "local", "api")]
    [string]$EmbeddingProvider = "hash"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root
$env:EMBEDDING_PROVIDER = $EmbeddingProvider

$netstat = Join-Path $env:SystemRoot "System32\netstat.exe"
$python = $null
$searchingPython = "D:\Documents\conda\conda_envs\searching\python.exe"
if (Test-Path -LiteralPath $searchingPython) {
    $python = $searchingPython
}

$conda = Get-Command conda -ErrorAction SilentlyContinue
if (-not $python -and $conda) {
    try {
        $condaPython = & $conda.Source run -n searching python -c "import sys; print(sys.executable)"
        if ($LASTEXITCODE -eq 0 -and $condaPython -and (Test-Path -LiteralPath $condaPython.Trim())) {
            $python = $condaPython.Trim()
        }
    }
    catch {
        $python = $null
    }
}

if (-not $python) {
    $legacyPython = "C:\Users\zj\AppData\Local\Programs\Python\Python312\python.exe"
    if (Test-Path -LiteralPath $legacyPython) {
        $python = $legacyPython
    }
    else {
        $python = (Get-Command python).Source
    }
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

$cmdLine = "set EMBEDDING_PROVIDER=$EmbeddingProvider&& start ""SEU Search Server"" /D ""$root"" /MIN ""$python"" -m uvicorn backend.app.web.main:app --host 127.0.0.1 --port $Port"
Start-Process `
    -FilePath $env:ComSpec `
    -ArgumentList @("/c", $cmdLine) `
    -WorkingDirectory $root `
    -WindowStyle Hidden | Out-Null

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
    throw "Failed to start server on http://127.0.0.1:$Port/"
}

Write-Output "SEU Search server started: http://127.0.0.1:$Port/"
$serverPids = & $netstat -ano |
    Select-String ":$Port\s+.*LISTENING\s+(\d+)" |
    ForEach-Object { [regex]::Match($_.Line, "LISTENING\s+(\d+)").Groups[1].Value } |
    Select-Object -Unique
if ($serverPids) {
    Write-Output "PID: $($serverPids -join ', ')"
}
Write-Output "Documents: $($health.documents), chunks: $($health.chunks)"
