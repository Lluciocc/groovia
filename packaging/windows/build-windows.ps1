[CmdletBinding()]
param(
    [switch]$SkipInstaller,
    [switch]$Console
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildRoot = Join-Path $RepoRoot "build\windows"
$PackageRoot = Join-Path $BuildRoot "package\groovia"
$DistRoot = Join-Path $RepoRoot "dist"
$AppRoot = Join-Path $DistRoot "Groovia"
$InstallerRoot = Join-Path $DistRoot "installer"

function Require-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing '$Name'. $Hint"
    }
}

Require-Command "pyinstaller" "Install it in the MSYS2 UCRT64 Python environment."
Require-Command "glib-compile-resources" "Install mingw-w64-ucrt-x86_64-glib2."
Require-Command "glib-compile-schemas" "Install mingw-w64-ucrt-x86_64-glib2."
if (-not $SkipInstaller) {
    Require-Command "iscc" "Install Inno Setup and make iscc.exe available on PATH."
}

New-Item -ItemType Directory -Force -Path $BuildRoot, $DistRoot | Out-Null
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
& glib-compile-resources `
    --sourcedir (Join-Path $RepoRoot "src") `
    --target (Join-Path $BuildRoot "groovia.gresource") `
    (Join-Path $RepoRoot "src\groovia.gresource.xml")
if ($LASTEXITCODE -ne 0) { throw "glib-compile-resources failed" }

& glib-compile-schemas `
    --targetdir (Join-Path $BuildRoot "schemas") `
    (Join-Path $RepoRoot "data")
if ($LASTEXITCODE -ne 0) { throw "glib-compile-schemas failed" }

& (Join-Path $PSScriptRoot "stage-dependencies.ps1") `
    -ManifestPath (Join-Path $PSScriptRoot "dependencies.json") `
    -OutputDir (Join-Path $BuildRoot "tools") `
    -LicenseDir (Join-Path $BuildRoot "licenses")
if ($LASTEXITCODE -ne 0) { throw "Windows downloader dependency staging failed" }

# PyInstaller and Inno Setup both require an ICO.  ImageMagick converts the
# maintained GNOME SVG without adding a second icon source to the repository.
Require-Command "magick" "Install ImageMagick to convert the application SVG to an ICO."
& magick (Join-Path $RepoRoot "data\icons\hicolor\scalable\apps\io.github.Lluciocc.Groovia.svg") `
    -background none -define icon:auto-resize=256,128,64,48,32,16 `
    (Join-Path $BuildRoot "Groovia.ico")
if ($LASTEXITCODE -ne 0) { throw "ImageMagick icon conversion failed" }

& pyinstaller --noconfirm --clean `
    --distpath $DistRoot `
    --workpath (Join-Path $RepoRoot "build\pyinstaller") `
    (Join-Path $RepoRoot "packaging\windows\Groovia.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$Exe = Join-Path $AppRoot "Groovia.exe"
if (-not (Test-Path -LiteralPath $Exe)) { throw "PyInstaller output is missing: $Exe" }
Write-Host "Validated standalone application: $Exe"

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
}
Write-Host "Validated Adwaita and hicolor icon themes ($($RequiredIconNames.Count) standard icons plus Groovia icons)"

& python (Join-Path $PSScriptRoot "smoke-test-icons.py") --bundle-root $AppRoot
if ($LASTEXITCODE -ne 0) { throw "Bundled GTK icon theme smoke test failed" }

$PackagedToolsRoot = Join-Path $AppRoot "_internal\tools"

if (-not (Test-Path -LiteralPath $PackagedToolsRoot)) {
    throw "Packaged tools directory is missing: $PackagedToolsRoot"
}

& (Join-Path $PSScriptRoot "smoke-test-tools.ps1") `
    -ToolsRoot $PackagedToolsRoot

if ($LASTEXITCODE -ne 0) {
    throw "Packaged downloader tool smoke test failed"
}
if ($LASTEXITCODE -ne 0) { throw "Packaged downloader tool smoke test failed" }

if (-not $SkipInstaller) {
    New-Item -ItemType Directory -Force -Path $InstallerRoot | Out-Null
    & iscc (Join-Path $RepoRoot "packaging\windows\Groovia.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    Write-Host "Installer output: $InstallerRoot"
}
