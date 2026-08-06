<img height="128" src="data/icons/hicolor/scalable/apps/io.github.Lluciocc.Groovia.svg" align="left"/>

# Groovia

Modern Music player

Groovia is a modern GNOME music player for local files, built with Python,
GTK 4, Libadwaita, GStreamer and SQLite. It presents a local collection as
an album-first library with an animated vinyl deck, queue and playback bar.

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
```

The reproducible build compiles the GResource and GSettings schema bundle,
creates a one-folder application at `dist/Groovia/`, validates
`dist/Groovia/Groovia.exe`, and writes the Inno Setup installer under
`dist/installer/`. Pass `-SkipInstaller` or `--skip-installer` when only the
PyInstaller directory is needed. The installed application uses
`%LOCALAPPDATA%\Programs\Groovia`; user music, database, lyrics, cache and
settings are outside that directory and survive upgrades and uninstall.

The bundled runtime includes the GStreamer core libraries, plugin DLLs,
plugin scanner, selected GObject typelibs, schemas and resources. The plugin
subset is sourced from the MSYS2 GStreamer installation rather than copying
the whole MSYS2 tree. Audio formats still depend on the plugins available in
the build environment. MPRIS is Linux-only and is skipped on Windows.

For Windows troubleshooting, run the build with `-SkipInstaller` first and
check `dist/Groovia/`. A missing schema, resource, typelib or GStreamer
scanner is reported by the runtime helper. spotDL is optional; a packaged
build cannot create a new Python virtual environment, so use a development
build or provide a pre-existing managed spotDL installation when downloads
are required.

## Spotify imports

Groovia uses the official spotDL command-line tool to find matching audio on
external providers and import Spotify metadata and artwork. Audio is not
downloaded directly from Spotify; users are responsible for copyright and
service terms.

The first import asks before installing missing dependencies. spotDL is
installed in Groovia's private environment under
`$XDG_DATA_HOME/groovia/downloader/venv`; FFmpeg and Deno can be installed in
the same managed environment. No `sudo` or system-Python modification is used.
The Preferences > Downloads page shows installation progress and includes a
confirmation action to remove Groovia-managed spotDL, FFmpeg and Deno files;
system-wide installations are never removed. These files live below
`$XDG_DATA_HOME/groovia/downloader/`.

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
