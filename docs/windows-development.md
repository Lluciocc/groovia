# Windows Development and Packaging

Windows support targets a native **MSYS2 UCRT64** environment.

Do not use the MSYS shell and do not install the runtime into an MSYS2
directory outside the UCRT64 environment.

## Install MSYS2 dependencies

Run the following from the **UCRT64 shell**:

```sh
pacman -S mingw-w64-ucrt-x86_64-python \
  mingw-w64-ucrt-x86_64-python-gobject \
  mingw-w64-ucrt-x86_64-gtk4 mingw-w64-ucrt-x86_64-libadwaita \
  mingw-w64-ucrt-x86_64-gstreamer mingw-w64-ucrt-x86_64-gst-plugins-base \
  mingw-w64-ucrt-x86_64-gst-plugins-good mingw-w64-ucrt-x86_64-gst-plugins-bad \
  mingw-w64-ucrt-x86_64-python-numpy mingw-w64-ucrt-x86_64-python-scipy \
  mingw-w64-ucrt-x86_64-glib2 mingw-w64-ucrt-x86_64-python-pip
```

NumPy and SciPy are official Auto DJ runtime dependencies.

The analyzer uses them for:

- onset envelopes;
- autocorrelation;
- filtering;
- FFT;
- feature statistics.

The `gst-plugins-bad` package supplies the optional `pitch` time-stretch
element. A Rubber Band element is preferred when that plugin is available.

If no pitch-preserving element is installed, Auto DJ remains usable but tempo
matching is disabled for that transition.

## Install PyInstaller

```sh
python -m pip install pyinstaller
```

## External tools

Install:

- Inno Setup
- ImageMagick

Make sure `iscc.exe` and `magick` are available to PowerShell.

## Build

From PowerShell:

```powershell
powershell.exe -ExecutionPolicy Bypass -File packaging/windows/build-windows.ps1
```

Or from MSYS2 UCRT64:

```sh
bash packaging/windows/build-windows.sh
```

For a console-enabled executable:

```sh
bash packaging/windows/build-windows.sh --console
```

PowerShell equivalent:

```powershell
.\packaging\windows\build-windows.ps1 -Console
```

To skip the installer:

```powershell
.\packaging\windows\build-windows.ps1 -SkipInstaller
```

or:

```sh
bash packaging/windows/build-windows.sh --skip-installer
```

## What the build does

The reproducible Windows build:

1. compiles the GResource bundle;
2. compiles the GSettings schema bundle;
3. creates the one-folder application under `dist/Groovia/`;
4. validates `dist/Groovia/Groovia.exe`;
5. runs `Groovia.exe --smoke-test`;
6. generates the Inno Setup installer under `dist/installer/`.

Before PyInstaller runs, the build imports NumPy and SciPy.

The final smoke test verifies:

- packaged NumPy and SciPy versions;
- SciPy DSP functionality;
- GStreamer;
- availability of a pitch-preserving tempo element.

## Installed application

The default installation location is:

```text
%LOCALAPPDATA%\Programs\Groovia
```

User music, database, lyrics, cache and settings are stored outside the
installation directory and survive upgrades and uninstall.

## Bundled runtime

The Windows bundle includes:

- GStreamer core libraries;
- selected GStreamer plugin DLLs;
- the GStreamer plugin scanner;
- selected GObject typelibs;
- schemas;
- GTK resources.

The plugin subset is copied from the MSYS2 GStreamer installation instead of
copying the complete MSYS2 tree.

Supported audio formats still depend on plugins available in the build
environment.

MPRIS is Linux-only and is skipped on Windows.

## Bundled downloader tools

The Windows build stages pinned native downloader tools into:

```text
dist/Groovia/tools/
```

Pinned versions:

- spotDL 4.5.2
- FFmpeg `autobuild-2026-08-06-13-39`
- Deno 2.9.4

FFmpeg includes:

- `ffmpeg.exe`
- `ffprobe.exe`

Artifact URLs, versions, SHA-256 hashes and license sources are defined in:

```text
packaging/windows/dependencies.json
```

This file is the single source of truth.

The build fails if an artifact is missing or if a hash does not match.

Version probes are also run against the final one-folder output.

The installed application therefore does not require:

- Python;
- pip;
- a virtual environment;
- Rust;
- MSYS2;
- an external downloader runtime.

## Updating bundled downloader dependencies

Update the relevant:

- URL;
- version;
- SHA-256;
- license hash;
- license source.

in:

```text
packaging/windows/dependencies.json
```

Then rerun:

```powershell
.\packaging\windows\build-windows.ps1
```

License files are retained in:

```text
dist/Groovia/licenses/
```

spotDL and Deno are distributed under their upstream licenses.

The selected FFmpeg build is GPL. Review the included license files before
publishing a release.

## Troubleshooting

Run the build with `-SkipInstaller` first and inspect:

```text
dist/Groovia/
```

A missing schema, resource, typelib or GStreamer scanner is reported by the
runtime helper.

In Groovia, use:

```text
Preferences > Downloads > Verify bundled tools
```

to capture the installed tool versions.

Reinstall Groovia to repair packaged tools.

The application never removes files directly from its installation directory.

## Recommended PowerShell environment

```powershell
$env:Path = @(
    "C:\msys64\ucrt64\bin"
    "C:\msys64\usr\bin"
    "C:\Program Files (x86)\Inno Setup 6"
    $env:Path
) -join ";"

Get-Process Groovia,spotdl,ffmpeg,ffprobe,deno `
    -ErrorAction SilentlyContinue |
    Stop-Process -Force

.\packaging\windows\build-windows.ps1
```
