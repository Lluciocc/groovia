# smoke-test-tools.ps1
#
# Copyright 2026 Lluciocc (llucio.cc00@gmail.com)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

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
