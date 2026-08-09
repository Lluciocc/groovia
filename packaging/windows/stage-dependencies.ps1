# stage-dependencies.ps1
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
    [string]$ManifestPath = (Join-Path $PSScriptRoot "dependencies.json"),
    [string]$OutputDir,
    [string]$LicenseDir,
    [string]$CacheDir,
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildRoot = Join-Path $RepoRoot "build\windows"
if (-not $OutputDir) { $OutputDir = Join-Path $BuildRoot "tools" }
if (-not $LicenseDir) { $LicenseDir = Join-Path $BuildRoot "licenses" }
if (-not $CacheDir) { $CacheDir = Join-Path $BuildRoot "dependency-cache" }

if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) {
    throw "Missing curl.exe. Install the standard Windows curl or MSYS2 curl package."
}

$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
foreach ($path in @($OutputDir, $LicenseDir, $CacheDir)) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
}
Remove-Item -LiteralPath $OutputDir -Recurse -Force
Remove-Item -LiteralPath $LicenseDir -Recurse -Force
New-Item -ItemType Directory -Force -Path $OutputDir, $LicenseDir | Out-Null

function Assert-Hash([string]$Path, [string]$Expected) {
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($actual -ne $Expected.ToUpperInvariant()) {
        throw "SHA-256 mismatch for '$Path': expected $Expected, got $actual"
    }
}

function Get-Artifact([object]$Spec, [string]$Name) {
    if ($Spec.url -match '/latest(?:/|$)' -or $Spec.url -match '/releases/latest') {
        throw "Unpinned latest URL is not allowed for $Name"
    }
    $cachePath = Join-Path $CacheDir $Spec.cache_name
    if (-not (Test-Path -LiteralPath $cachePath)) {
        if ($Offline) { throw "Missing cached artifact for offline build: $cachePath" }
        $downloadPath = "$cachePath.download"
        Remove-Item -LiteralPath $downloadPath -Force -ErrorAction SilentlyContinue
        Write-Host "Downloading pinned $Name artifact..."
        & curl.exe --fail --location --retry 3 --output $downloadPath $Spec.url
        if ($LASTEXITCODE -ne 0) { throw "Download failed for $Name" }
        Assert-Hash $downloadPath $Spec.sha256
        Move-Item -LiteralPath $downloadPath -Destination $cachePath
    } else {
        Assert-Hash $cachePath $Spec.sha256
    }
    return (Resolve-Path -LiteralPath $cachePath).Path
}

function Stage-License([object]$Spec, [string]$Name) {
    if (-not $Spec.license_url) { return }
    $licenseSpec = [pscustomobject]@{
        url = $Spec.license_url
        sha256 = $Spec.license_sha256
        cache_name = $Spec.license_name
    }
    $licensePath = Get-Artifact $licenseSpec "$Name license"
    Copy-Item -LiteralPath $licensePath -Destination (Join-Path $LicenseDir $Spec.license_name)
}

function Expand-Artifact([string]$Archive, [string]$Name) {
    $extractRoot = Join-Path $BuildRoot "dependency-extract\$Name"
    if (Test-Path -LiteralPath $extractRoot) {
        Remove-Item -LiteralPath $extractRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
    Expand-Archive -LiteralPath $Archive -DestinationPath $extractRoot -Force
    return (Resolve-Path -LiteralPath $extractRoot).Path
}

foreach ($entry in $manifest.PSObject.Properties) {
    $name = $entry.Name
    $spec = $entry.Value
    $artifact = Get-Artifact $spec $name
    if ($spec.kind -eq "file") {
        Copy-Item -LiteralPath $artifact -Destination (Join-Path $OutputDir $spec.output)
    } elseif ($spec.kind -eq "archive") {
        $root = Expand-Artifact $artifact $name
        foreach ($file in $spec.files.PSObject.Properties) {
            $found = Get-ChildItem -LiteralPath $root -Recurse -File -Filter $file.Value | Select-Object -First 1
            if (-not $found) { throw "Expected $($file.Value) was not found in $name artifact" }
            Copy-Item -LiteralPath $found.FullName -Destination (Join-Path $OutputDir $file.Name)
        }
        if ($spec.license_globs) {
            $licenses = foreach ($pattern in $spec.license_globs) {
                Get-ChildItem -LiteralPath $root -Recurse -File -Filter $pattern
            }
            $licenses = @($licenses | Sort-Object FullName -Unique)
            if ($licenses.Count -eq 0) { throw "No license file was found in $name artifact" }
            foreach ($license in $licenses) {
                Copy-Item -LiteralPath $license.FullName -Destination (Join-Path $LicenseDir "$name-$($license.Name)")
            }
        }
    } else {
        throw "Unsupported artifact kind '$($spec.kind)' for $name"
    }
    Stage-License $spec $name
}

foreach ($required in @("spotdl.exe", "ffmpeg.exe", "ffprobe.exe", "deno.exe")) {
    $path = Join-Path $OutputDir $required
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Staging failed; missing $path"
    }
}
Write-Host "Staged pinned Windows downloader tools in $OutputDir"
