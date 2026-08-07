<img height="128" src="data/icons/hicolor/scalable/apps/io.github.Lluciocc.Groovia.svg" align="left"/>

# Groovia

Modern Music player

Groovia is a modern GNOME music player for local files, built with Python,
GTK 4, Libadwaita, GStreamer and SQLite. It presents a local collection as
an album-first library with an animated vinyl deck, queue and playback bar.

## Screenshots
<img width="1920" height="1035" alt="{D46C6171-D567-453B-BD1B-D00184C65209}" src="https://github.com/user-attachments/assets/7dc95176-be31-4583-9f61-b5b0b057bb5b" />

<img width="1920" height="1035" alt="{786BB400-C5C8-4AE4-85D9-785FBE804A8F}" src="https://github.com/user-attachments/assets/454a0073-e6e0-445c-80c4-7196bfd4315b" />


<img width="1920" height="1080" alt="{CBB96980-FCA6-4F4F-99A4-DAD3A711AB8E}" src="https://github.com/user-attachments/assets/cfeeb506-40fe-4acc-ac2d-6efe5deed8dc" />

## Development

The project is intended to be built with GNOME Builder or Meson. The runtime
needs GTK 4, Libadwaita, PyGObject and the GStreamer base/good/bad plugins.

```sh
meson setup build
meson compile -C build
./build/src/groovia
```

The interface is intentionally English for this first release.

Linux is Groovia's primary supported platform. The Meson, GNOME and Flatpak
builds remain the reference implementations and retain XDG paths, GSettings,
GTK resources and MPRIS integration.

## Windows development and packaging

Windows support targets a native MSYS2 UCRT64 environment. Install the
project's runtime packages and build tools in the UCRT64 shell; do not use the
MSYS shell or install into an MSYS2 directory:

```sh
pacman -S mingw-w64-ucrt-x86_64-python \
  mingw-w64-ucrt-x86_64-python-gobject \
  mingw-w64-ucrt-x86_64-gtk4 mingw-w64-ucrt-x86_64-libadwaita \
  mingw-w64-ucrt-x86_64-gstreamer mingw-w64-ucrt-x86_64-gst-plugins-base \
  mingw-w64-ucrt-x86_64-gst-plugins-good mingw-w64-ucrt-x86_64-gst-plugins-bad \
  mingw-w64-ucrt-x86_64-glib2 mingw-w64-ucrt-x86_64-python-pip
```

Then install the pinned build tool into that UCRT64 Python environment with
`python -m pip install pyinstaller`.

Install Inno Setup and ImageMagick on Windows, with `iscc.exe` and `magick`
available to PowerShell. From PowerShell or the UCRT64 shell:

```powershell
powershell.exe -ExecutionPolicy Bypass -File packaging/windows/build-windows.ps1
# Or, from MSYS2 UCRT64:
bash packaging/windows/build-windows.sh
# Keep a console attached to the Windows executable when debugging:
bash packaging/windows/build-windows.sh --console
```

The reproducible build compiles the GResource and GSettings schema bundle,
creates a one-folder application at `dist/Groovia/`, validates
`dist/Groovia/Groovia.exe`, and writes the Inno Setup installer under
`dist/installer/`. Pass `-SkipInstaller` or `--skip-installer` when only the
PyInstaller directory is needed. Pass `-Console` to the PowerShell script, or
`--console` to the Bash wrapper, to build a console-enabled executable. The
installed application uses
`%LOCALAPPDATA%\Programs\Groovia`; user music, database, lyrics, cache and
settings are outside that directory and survive upgrades and uninstall.

The bundled runtime includes the GStreamer core libraries, plugin DLLs,
plugin scanner, selected GObject typelibs, schemas and resources. The plugin
subset is sourced from the MSYS2 GStreamer installation rather than copying
the whole MSYS2 tree. Audio formats still depend on the plugins available in
the build environment. MPRIS is Linux-only and is skipped on Windows.

The Windows build stages native, pinned downloader tools into
`dist/Groovia/tools/`: spotDL 4.5.2, FFmpeg
`autobuild-2026-08-06-13-39` (including `ffmpeg.exe` and `ffprobe.exe`) and
Deno 2.9.4. Their SHA-256 hashes, artifact URLs and license sources are the
single source of truth in `packaging/windows/dependencies.json`. The build
fails on a missing or mismatched artifact and runs version probes from the
final one-folder output. The installed application therefore needs no Python,
pip, virtual environment, Rust, MSYS2 or external downloader runtime.

For Windows troubleshooting, run the build with `-SkipInstaller` first and
check `dist/Groovia/`. A missing schema, resource, typelib or GStreamer
scanner is reported by the runtime helper. Use Preferences > Downloads >
Verify bundled tools to capture the four tool versions. Reinstall Groovia to
repair packaged tools; the application never removes files from its
installation directory.

## Build for windows
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

## Spotify imports

Groovia uses the official spotDL command-line tool to find matching audio on
external providers and import Spotify metadata and artwork. Audio is not
downloaded directly from Spotify; users are responsible for copyright and
service terms.

On Linux, the first import asks before installing spotDL in Groovia's private
environment under `$XDG_DATA_HOME/groovia/downloader/venv`; the existing Linux
FFmpeg and Deno management flow remains unchanged. On Windows, the installer
supplies the native executables under `{app}\tools` and the Preferences page
only verifies them. The Inno Setup uninstaller owns those packaged files;
music, database, lyrics, cache and configuration remain outside `{app}`.

To update a Windows downloader dependency, change its pinned URL, version and
SHA-256 in `packaging/windows/dependencies.json`, update its license hash and
source if needed, then rerun `build-windows.ps1`. The staging step retains
license files in `dist/Groovia/licenses/`. spotDL is distributed under its
upstream license, Deno under its upstream license, and the selected FFmpeg
build is GPL; review the included license files before publishing.

Spotify files are written to `Music/Groovia`, with synchronized playlists in
`Music/Groovia/Synced Playlists`. The Flatpak requests network access and
scoped read/write access to the user's Music directory so these configured
destinations work inside the sandbox; no home-directory permission is used.

Lyrics are optional. When enabled, spotDL receives the configured `--lyrics`
providers and generates local `.lrc` files when the installed version supports
`--generate-lrc`. Groovia keeps lyrics mappings in SQLite and stores manually
imported lyrics under `$XDG_DATA_HOME/groovia/lyrics`; edited lyrics are
preserved during later downloads and synchronization.

Auto DJ is an opt-in playback enhancement. It analyzes the current and next
track in a background worker, caches the result under
`$XDG_DATA_HOME/groovia/autodj/analysis.json`, preloads one next stream and
uses the existing queue as its only source of tracks. With Auto DJ disabled,
the original crossfade path is retained unchanged. Auto DJ never reorders or
duplicates queue entries.
