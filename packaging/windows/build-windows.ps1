[CmdletBinding()]
param(
    [switch]$SkipInstaller
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
& glib-compile-resources `
    --sourcedir (Join-Path $RepoRoot "src") `
    --target (Join-Path $BuildRoot "groovia.gresource") `
    (Join-Path $RepoRoot "src\groovia.gresource.xml")
if ($LASTEXITCODE -ne 0) { throw "glib-compile-resources failed" }

& glib-compile-schemas `
    --targetdir (Join-Path $BuildRoot "schemas") `
    (Join-Path $RepoRoot "data")
if ($LASTEXITCODE -ne 0) { throw "glib-compile-schemas failed" }

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

if (-not $SkipInstaller) {
    New-Item -ItemType Directory -Force -Path $InstallerRoot | Out-Null
    & iscc (Join-Path $RepoRoot "packaging\windows\Groovia.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    Write-Host "Installer output: $InstallerRoot"
}
