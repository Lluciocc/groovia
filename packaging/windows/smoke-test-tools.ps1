[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ToolsRoot
)

$ErrorActionPreference = "Stop"
$ToolsRoot = (Resolve-Path -LiteralPath $ToolsRoot).Path
$probes = @(
    @("spotdl.exe", "--version"),
    @("ffmpeg.exe", "-version"),
    @("deno.exe", "--version")
)
Push-Location $ToolsRoot
try {
    foreach ($probe in $probes) {
        $path = Join-Path $ToolsRoot $probe[0]
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Missing packaged tool: $path"
        }
        Write-Host "Testing $($probe[0]) $($probe[1])"
        $output = & $path $probe[1] 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "$($probe[0]) version probe failed with exit code $LASTEXITCODE`n$output"
        }
        $output | Select-Object -First 2 | ForEach-Object { Write-Host "  $_" }
    }
} finally {
    Pop-Location
}
