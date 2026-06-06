param(
    [int]$Port = 8000,
    [switch]$SeedDemo,
    [switch]$Foreground,
    [switch]$Lan
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root

function Resolve-Python {
    $venvPython = Join-Path $root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    $candidates = @(
        "C:\Python314\python.exe",
        "C:\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python314\python.exe",
        "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python313\python.exe",
        "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python312\python.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }

    $pyCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCommand) {
        return $pyCommand.Source
    }

    throw "Python not found. Install Python or create .venv first."
}

function Ensure-Venv {
    param(
        [string]$BootstrapPython
    )

    $venvDir = Join-Path $root ".venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    Write-Host "Creating virtual environment..."
    & $BootstrapPython -m venv $venvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
        throw "Failed to create virtual environment at $venvDir."
    }

    return $venvPython
}

function Ensure-Dependencies {
    param(
        [string]$VenvPython
    )

    $requirementsPath = Join-Path $root "backend\requirements.txt"
    $stampPath = Join-Path $root ".venv\.requirements.sha256"
    $requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash
    $installedHash = ""

    if (Test-Path -LiteralPath $stampPath) {
        $installedHash = (Get-Content -LiteralPath $stampPath -Raw).Trim()
    }

    $needInstall = $requirementsHash -ne $installedHash
    if (-not $needInstall) {
        & $VenvPython -c "import fastapi, uvicorn, pydantic"
        if ($LASTEXITCODE -eq 0) {
            return
        }
        $needInstall = $true
    }

    Write-Output "Installing Python dependencies..."
    & $VenvPython -m pip install -r $requirementsPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install Python dependencies from $requirementsPath."
    }

    Set-Content -LiteralPath $stampPath -Value $requirementsHash -Encoding ASCII
}

function Ensure-LanFirewallRule {
    param(
        [int]$Port
    )

    try {
        $ruleName = "SEU Search LAN $Port"
        $existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue

        if ($existingRule) {
            Set-NetFirewallRule -DisplayName $ruleName -Enabled True -Direction Inbound -Action Allow -Profile Private,Public -ErrorAction Stop | Out-Null
            Set-NetFirewallAddressFilter -AssociatedNetFirewallRule $existingRule -RemoteAddress LocalSubnet -ErrorAction Stop | Out-Null
            Set-NetFirewallPortFilter -AssociatedNetFirewallRule $existingRule -Protocol TCP -LocalPort $Port -ErrorAction Stop | Out-Null
            return
        }

        New-NetFirewallRule `
            -DisplayName $ruleName `
            -Direction Inbound `
            -Action Allow `
            -Protocol TCP `
            -LocalPort $Port `
            -Profile Private,Public `
            -RemoteAddress LocalSubnet `
            -ErrorAction Stop | Out-Null
    }
    catch {
        Write-Warning "Could not create Windows Firewall rule automatically. Run PowerShell as Administrator once, or allow inbound TCP $Port manually for local subnet access."
    }
}

function Get-LanAccessUrls {
    param(
        [int]$Port
    )

    $urls = [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
        Where-Object {
            $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
            -not $_.IPAddressToString.StartsWith("127.")
        } |
        ForEach-Object { "http://$($_.IPAddressToString):$Port/" } |
        Sort-Object -Unique

    return @($urls)
}

# Some Codex/Windows shells expose both Path and PATH. PowerShell Start-Process
# treats environment keys case-insensitively and fails unless the duplicate is removed.
[Environment]::SetEnvironmentVariable("PATH", $null, "Process")

$netstat = Join-Path $env:SystemRoot "System32\netstat.exe"
$python = Ensure-Venv (Resolve-Python)
Ensure-Dependencies $python
$bindHost = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" }
$localUrl = "http://127.0.0.1:$Port/"
$healthUrl = "http://127.0.0.1:$Port/api/health"
$lanUrls = if ($Lan) { Get-LanAccessUrls -Port $Port } else { @() }

if ($Lan) {
    Ensure-LanFirewallRule -Port $Port
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

if ($SeedDemo) {
    & $python -m backend.app.seed_demo
}

$uvicornArgs = @("-m", "uvicorn", "backend.app.web.main:app", "--host", $bindHost, "--port", "$Port")

if ($Foreground) {
    Write-Output "SEU Search server starting: $localUrl"
    if ($Lan) {
        Write-Output "LAN mode enabled. Remote devices can search, but index management stays local-only."
        foreach ($url in $lanUrls) {
            Write-Output "LAN access: $url"
        }
    }
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
        $health = Invoke-RestMethod $healthUrl -TimeoutSec 3
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
    throw "Failed to start server on $localUrl"
}

Write-Output "SEU Search server started: $localUrl"
if ($Lan) {
    Write-Output "LAN mode enabled. Remote devices can search, but index management stays local-only."
    foreach ($url in $lanUrls) {
        Write-Output "LAN access: $url"
    }
}
Write-Output "PID: $($proc.Id)"
Write-Output "Documents: $($health.documents), chunks: $($health.chunks)"
