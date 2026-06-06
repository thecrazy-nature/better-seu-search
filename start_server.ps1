param(
    [int]$Port = 8000,
    [switch]$Foreground,
    [switch]$Lan,
    [string]$EmbeddingProvider = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvDir = Join-Path $root ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$requirementsPath = Join-Path $root "backend\requirements.txt"
$hostAddress = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" }

function Resolve-Python {
    if (Test-Path $venvPython) {
        return $venvPython
    }

    $candidates = @(
        "C:\Python314\python.exe",
        "C:\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    try {
        $python = (Get-Command python -ErrorAction Stop).Source
        if ($python) {
            return $python
        }
    }
    catch {
    }

    try {
        $py = (Get-Command py -ErrorAction Stop).Source
        if ($py) {
            return "$py -3"
        }
    }
    catch {
    }

    throw "Python was not found. Install Python first, then run this script again."
}

function Invoke-PythonCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonCommand,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    if ($PythonCommand.Contains(" ")) {
        $segments = $PythonCommand.Split(" ", 2)
        & $segments[0] $segments[1] @Arguments
    }
    else {
        & $PythonCommand @Arguments
    }
}

function Ensure-Venv {
    param([string]$BootstrapPython)

    if (-not (Test-Path $venvPython)) {
        Write-Output "Creating .venv ..."
        Invoke-PythonCommand -PythonCommand $BootstrapPython -Arguments @("-m", "venv", $venvDir)
    }
}

function Ensure-Dependencies {
    if (-not (Test-Path $requirementsPath)) {
        return
    }

    $marker = Join-Path $venvDir ".deps_ready"
    $needsInstall = -not (Test-Path $marker)
    if (-not $needsInstall) {
        $markerTime = (Get-Item $marker).LastWriteTimeUtc
        $requirementsTime = (Get-Item $requirementsPath).LastWriteTimeUtc
        $needsInstall = $requirementsTime -gt $markerTime
    }

    if ($needsInstall) {
        Write-Output "Installing backend dependencies ..."
        & $venvPython -m pip install --upgrade pip
        & $venvPython -m pip install -r $requirementsPath
        Set-Content -Path $marker -Value (Get-Date).ToString("s")
    }
}

function Ensure-FirewallRule {
    param([int]$RulePort)

    if (-not $Lan) {
        return
    }

    try {
        $existing = Get-NetFirewallRule -DisplayName "SEU Search LAN $RulePort" -ErrorAction SilentlyContinue
        if (-not $existing) {
            New-NetFirewallRule `
                -DisplayName "SEU Search LAN $RulePort" `
                -Direction Inbound `
                -Action Allow `
                -Protocol TCP `
                -LocalPort $RulePort `
                -Profile Private,Public `
                -RemoteAddress LocalSubnet `
                | Out-Null
        }
    }
    catch {
        Write-Output "Firewall rule was not added automatically. If remote devices still cannot connect, run this script as Administrator once."
    }
}

function Get-LanAddresses {
    $addresses = @()
    try {
        $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object {
                $_.IPAddress -notlike "127.*" -and
                $_.IPAddress -notlike "169.254.*" -and
                $_.PrefixOrigin -ne "WellKnown"
            } |
            Select-Object -ExpandProperty IPAddress -Unique
    }
    catch {
        try {
            $addresses = [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
                Where-Object { $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork } |
                ForEach-Object { $_.IPAddressToString } |
                Where-Object { $_ -notlike "127.*" -and $_ -notlike "169.254.*" } |
                Select-Object -Unique
        }
        catch {
            $addresses = @()
        }
    }
    return @($addresses)
}

$bootstrapPython = Resolve-Python
Ensure-Venv -BootstrapPython $bootstrapPython
Ensure-Dependencies
Ensure-FirewallRule -RulePort $Port

if (-not $EmbeddingProvider) {
    $EmbeddingProvider = $env:EMBEDDING_PROVIDER
}

$uvicornArgs = @("-m", "uvicorn", "backend.app.web.main:app", "--host", $hostAddress, "--port", "$Port")

if ($Foreground) {
    $env:EMBEDDING_PROVIDER = $EmbeddingProvider
    if ($Lan) {
        Write-Output "LAN mode enabled. Remote devices can search, but index management stays local-only."
        foreach ($ip in (Get-LanAddresses)) {
            Write-Output ("LAN access: http://{0}:{1}/" -f $ip, $Port)
        }
    }
    else {
        Write-Output "SEU Search server starting: http://127.0.0.1:$Port/"
    }
    Push-Location $root
    try {
        & $venvPython @uvicornArgs
    }
    finally {
        Pop-Location
    }
    exit $LASTEXITCODE
}

$env:EMBEDDING_PROVIDER = $EmbeddingProvider
$process = Start-Process `
    -FilePath $venvPython `
    -ArgumentList $uvicornArgs `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -PassThru

$started = $false
for ($i = 0; $i -lt 25; $i++) {
    Start-Sleep -Milliseconds 600
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -TimeoutSec 3
        if ($health.ok) {
            $started = $true
            break
        }
    }
    catch {
    }
}

if (-not $started) {
    if ($process -and -not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
    }
    throw "Failed to start server on http://127.0.0.1:$Port/"
}

if ($Lan) {
    Write-Output "LAN mode enabled. Remote devices can search, but index management stays local-only."
    foreach ($ip in (Get-LanAddresses)) {
        Write-Output ("LAN access: http://{0}:{1}/" -f $ip, $Port)
    }
}
else {
    Write-Output "SEU Search server started: http://127.0.0.1:$Port/"
}
