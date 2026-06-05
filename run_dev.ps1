param(
    [switch]$SeedDemo
)

& "$PSScriptRoot\start_server.ps1" -SeedDemo:$SeedDemo -Foreground
