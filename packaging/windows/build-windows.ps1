# build-windows.ps1
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
    [switch]$SkipInstaller,
    [switch]$Console,
    [switch]$DontOpenDist
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildRoot = Join-Path $RepoRoot "build\windows"
$PackageRoot = Join-Path $BuildRoot "package\groovia"
$DistRoot = Join-Path $RepoRoot "dist"
$AppRoot = Join-Path $DistRoot "Groovia"
$InstallerRoot = Join-Path $DistRoot "installer"
$BuildTimer = $null
$InstallerTimer = $null
$TotalTimer = $null

function Write-Instruction([string]$Title, [string]$Message) {
    # Color the instruction title and keep informational text readable in gray.
    Write-Host -NoNewline -ForegroundColor Cyan ("[{0}] " -f $Title)
    Write-Host $Message -ForegroundColor Gray
}

function Write-Status([string]$Label, [string]$Message, [ConsoleColor]$Color = [ConsoleColor]::Gray) {
    # Status colors apply only to the label; informational details stay gray.
    Write-Host -NoNewline -ForegroundColor $Color ("{0}: " -f $Label)
    Write-Host $Message -ForegroundColor Gray
}

function Write-NativeOutput([object]$Output) {
    $Text = [string]$Output
    if ($Text -match "(?i)\b(error|fatal|failed)\b") {
        Write-Host $Text -ForegroundColor Red
    } elseif ($Text -match "(?i)\bwarning\b") {
        Write-Host $Text -ForegroundColor DarkYellow
    } elseif ($Text -match "(?i)\binfo\b") {
        Write-Host $Text -ForegroundColor Gray
    } else {
        Write-Host $Text
    }
}

function Invoke-NativeLogged([scriptblock]$Command) {
    # Native tools do not inherit PowerShell's warning/error colors. Merge both
    # streams and classify each emitted line without changing its text.
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        # PyInstaller writes normal progress messages to stderr. With Stop,
        # PowerShell turns those lines into NativeCommandError exceptions.
        $ErrorActionPreference = "Continue"
        & $Command 2>&1 | ForEach-Object { Write-NativeOutput $_ }
    } finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
}

function Format-Duration([TimeSpan]$Duration) {
    return "{0:00}:{1:00}:{2:00}.{3:000}" -f `
        [Math]::Floor($Duration.TotalHours), $Duration.Minutes, $Duration.Seconds, $Duration.Milliseconds
}

function Require-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing '$Name'. $Hint"
    }
}

Require-Command "pyinstaller" "Install it in the MSYS2 UCRT64 Python environment."
Invoke-NativeLogged { & python -c "import numpy, scipy; print('NumPy', numpy.__version__, 'SciPy', scipy.__version__)" }
if ($LASTEXITCODE -ne 0) {
    throw "NumPy and SciPy are required in the MSYS2 UCRT64 Python environment. Install mingw-w64-ucrt-x86_64-python-numpy and mingw-w64-ucrt-x86_64-python-scipy."
}
Require-Command "glib-compile-resources" "Install mingw-w64-ucrt-x86_64-glib2."
Require-Command "glib-compile-schemas" "Install mingw-w64-ucrt-x86_64-glib2."
if (-not $SkipInstaller) {
    Require-Command "iscc" "Install Inno Setup and make iscc.exe available on PATH."
}

New-Item -ItemType Directory -Force -Path $BuildRoot, $DistRoot | Out-Null
$TotalTimer = [System.Diagnostics.Stopwatch]::StartNew()
$BuildTimer = [System.Diagnostics.Stopwatch]::StartNew()
Write-Instruction "BUILD" "Preparing Windows build outputs"
foreach ($Target in @($AppRoot, $InstallerRoot, (Join-Path $RepoRoot "build\pyinstaller"), (Join-Path $BuildRoot "package"))) {
    if (Test-Path -LiteralPath $Target) {
        $ResolvedTarget = (Resolve-Path -LiteralPath $Target).Path
        if (-not $ResolvedTarget.StartsWith($RepoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean a path outside the repository: $ResolvedTarget"
        }
        Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force
    }
}
New-Item -ItemType Directory -Force -Path (Join-Path $BuildRoot "schemas") | Out-Null
New-Item -ItemType Directory -Force -Path $PackageRoot | Out-Null
Copy-Item -Path (Join-Path $RepoRoot "src\*.py") -Destination $PackageRoot -Force
Get-ChildItem -LiteralPath (Join-Path $RepoRoot "src") -Directory | Where-Object Name -ne "__pycache__" | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $PackageRoot -Recurse -Force
}

$env:GROOVIA_WINDOWS_BUILD_DIR = $BuildRoot
$env:GROOVIA_WINDOWS_CONSOLE = if ($Console) { "1" } else { "0" }
Write-Instruction "RESOURCES" "Compiling application resources and schemas"
Invoke-NativeLogged { & glib-compile-resources `
    --sourcedir (Join-Path $RepoRoot "src") `
    --target (Join-Path $BuildRoot "groovia.gresource") `
    (Join-Path $RepoRoot "src\groovia.gresource.xml") }
if ($LASTEXITCODE -ne 0) { throw "glib-compile-resources failed" }

Invoke-NativeLogged { & glib-compile-schemas `
    --targetdir (Join-Path $BuildRoot "schemas") `
    (Join-Path $RepoRoot "data") }
if ($LASTEXITCODE -ne 0) { throw "glib-compile-schemas failed" }

Invoke-NativeLogged { & (Join-Path $PSScriptRoot "stage-dependencies.ps1") `
    -ManifestPath (Join-Path $PSScriptRoot "dependencies.json") `
    -OutputDir (Join-Path $BuildRoot "tools") `
    -LicenseDir (Join-Path $BuildRoot "licenses") }
if ($LASTEXITCODE -ne 0) { throw "Windows downloader dependency staging failed" }

# PyInstaller and Inno Setup both require an ICO.  ImageMagick converts the
# maintained GNOME SVG without adding a second icon source to the repository.
Require-Command "magick" "Install ImageMagick to convert the application SVG to an ICO."
Write-Instruction "PACKAGE" "Building the standalone application"
Invoke-NativeLogged { & magick -background none `
    (Join-Path $RepoRoot "data\icons\hicolor\scalable\apps\io.github.Lluciocc.Groovia.svg") `
    -define icon:auto-resize=256,128,64,48,32,16 `
    (Join-Path $BuildRoot "Groovia.ico") }
if ($LASTEXITCODE -ne 0) { throw "ImageMagick icon conversion failed" }

Invoke-NativeLogged { & pyinstaller --noconfirm --clean `
    --distpath $DistRoot `
    --workpath (Join-Path $RepoRoot "build\pyinstaller") `
    (Join-Path $RepoRoot "packaging\windows\Groovia.spec") }
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$Exe = Join-Path $AppRoot "Groovia.exe"
if (-not (Test-Path -LiteralPath $Exe)) { throw "PyInstaller output is missing: $Exe" }
Write-Instruction "VALIDATE" "Standalone application: $Exe"

Invoke-NativeLogged { & $Exe --smoke-test }
if ($LASTEXITCODE -ne 0) { throw "Packaged Auto DJ NumPy/SciPy/GStreamer smoke test failed" }

$IconRoot = Join-Path $AppRoot "_internal\share\icons"
$AdwaitaIconRoot = Join-Path $IconRoot "Adwaita"
$HicolorIconRoot = Join-Path $IconRoot "hicolor"
foreach ($ThemeRoot in @($AdwaitaIconRoot, $HicolorIconRoot)) {
    if (-not (Test-Path -LiteralPath (Join-Path $ThemeRoot "index.theme") -PathType Leaf)) {
        throw "Packaged icon theme metadata is missing: $(Join-Path $ThemeRoot 'index.theme')"
    }
}

$RequiredIconNames = @(
    "audio-x-generic-symbolic",
    "open-menu-symbolic",
    "list-add-symbolic",
    "folder-music-symbolic",
    "document-save-symbolic",
    "media-playback-start-symbolic",
    "go-previous-symbolic",
    "find-location-symbolic",
    "view-fullscreen-symbolic",
    "text-x-generic-symbolic",
    "system-search-symbolic",
    "document-open-symbolic",
    "image-x-generic-symbolic",
    "view-restore-symbolic",
    "media-playlist-shuffle-symbolic",
    "view-more-symbolic",
    "view-refresh-symbolic",
    "starred-symbolic",
    "view-list-symbolic",
    "audio-volume-high-symbolic",
    "media-skip-forward-symbolic",
    "media-skip-backward-symbolic",
    "media-playlist-repeat-symbolic",
    "media-playlist-repeat-song-symbolic",
    "edit-paste-symbolic",
    "media-playback-pause-symbolic",
    "applications-graphics-symbolic",
    "edit-delete-symbolic",
    "sidebar-show-symbolic",
    "go-home-symbolic"
)
foreach ($IconName in $RequiredIconNames) {
    $Match = Get-ChildItem -LiteralPath $IconRoot -Recurse -File | Where-Object { $_.BaseName -eq $IconName } | Select-Object -First 1
    if (-not $Match) {
        throw "Required Groovia icon is absent from the packaged themes: $IconName (searched recursively below $IconRoot)"
    }
}
foreach ($IconName in @("io.github.Lluciocc.Groovia", "io.github.Lluciocc.Groovia-symbolic")) {
    $Match = Get-ChildItem -LiteralPath $HicolorIconRoot -Recurse -File | Where-Object { $_.BaseName -eq $IconName } | Select-Object -First 1
    if (-not $Match) {
        throw "Groovia application icon is absent from the packaged hicolor theme: $IconName"
    }
    $Match = Get-ChildItem -LiteralPath $AdwaitaIconRoot -Recurse -File | Where-Object { $_.BaseName -eq $IconName } | Select-Object -First 1
    if (-not $Match) {
        throw "Groovia application icon is absent from the packaged Adwaita theme: $IconName"
    }
}
Write-Instruction "VALIDATE" "Adwaita and hicolor icon themes ($($RequiredIconNames.Count) standard icons plus Groovia icons)"

Invoke-NativeLogged { & python (Join-Path $PSScriptRoot "smoke-test-icons.py") --bundle-root $AppRoot }
if ($LASTEXITCODE -ne 0) { throw "Bundled GTK icon theme smoke test failed" }

$PackagedToolsRoot = Join-Path $AppRoot "_internal\tools"

if (-not (Test-Path -LiteralPath $PackagedToolsRoot)) {
    throw "Packaged tools directory is missing: $PackagedToolsRoot"
}

Invoke-NativeLogged { & (Join-Path $PSScriptRoot "smoke-test-tools.ps1") `
    -ToolsRoot $PackagedToolsRoot }

if ($LASTEXITCODE -ne 0) {
    throw "Packaged downloader tool smoke test failed"
}

# PowerShell has no native orange console color. DarkYellow is the closest
# portable console color and keeps warnings distinct from normal output.
if ($Host.PrivateData) {
    $Host.PrivateData.WarningForegroundColor = "DarkYellow"
    $Host.PrivateData.ErrorForegroundColor = "Red"
}
if ($LASTEXITCODE -ne 0) { throw "Packaged downloader tool smoke test failed" }

$BuildTimer.Stop()

if (-not $SkipInstaller) {
    $InstallerTimer = [System.Diagnostics.Stopwatch]::StartNew()
    Write-Instruction "INSTALLER" "Generating the Inno Setup installer"
    New-Item -ItemType Directory -Force -Path $InstallerRoot | Out-Null
    Invoke-NativeLogged { & iscc (Join-Path $RepoRoot "packaging\windows\Groovia.iss") }
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    $InstallerTimer.Stop()
    Write-Instruction "VALIDATE" "Installer output: $InstallerRoot"
    $TotalTimer.Stop()
    $TotalElapsed = $TotalTimer.Elapsed
} else {
    # With no installer phase, total is exactly the work done by the build.
    $TotalTimer.Stop()
    $TotalElapsed = $BuildTimer.Elapsed
}

Write-Host ""
Write-Host -NoNewline -ForegroundColor Green "READY"
Write-Host ("  total: {0}  build: {1}" -f (Format-Duration $TotalElapsed), (Format-Duration $BuildTimer.Elapsed)) -ForegroundColor Gray
if (-not $SkipInstaller) {
    Write-Status "  installer" (Format-Duration $InstallerTimer.Elapsed)
}
if ($SkipInstaller) {
    Write-Status "  installer" "skipped" ([ConsoleColor]::DarkYellow)
}
if ($Console) {
    Write-Status "  console" "enabled" ([ConsoleColor]::Yellow)
}
if (-not $DontOpenDist) {
    Write-Instruction "OPEN" "Opening build output directory: $DistRoot"
    Start-Process -FilePath "explorer.exe" -ArgumentList $DistRoot
}
