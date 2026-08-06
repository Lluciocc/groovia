# Groovia Windows packaging

This is a native, one-folder PyInstaller build for MSYS2 UCRT64 followed by an
unprivileged Inno Setup installer. Linux/Meson/Flatpak remain the primary
support targets.

Required MSYS2 UCRT64 packages:

```sh
pacman -S mingw-w64-ucrt-x86_64-python \
  mingw-w64-ucrt-x86_64-python-gobject \
  mingw-w64-ucrt-x86_64-gtk4 mingw-w64-ucrt-x86_64-libadwaita \
  mingw-w64-ucrt-x86_64-gstreamer mingw-w64-ucrt-x86_64-gst-plugins-base \
  mingw-w64-ucrt-x86_64-gst-plugins-good mingw-w64-ucrt-x86_64-gst-plugins-bad \
  mingw-w64-ucrt-x86_64-glib2 mingw-w64-ucrt-x86_64-python-pip
```

Install PyInstaller explicitly into the UCRT64 Python with
`python -m pip install pyinstaller`. The packaging scripts never download
dependencies.

The Windows host also needs Inno Setup (`iscc.exe`) and ImageMagick
(`magick`) on PATH. The latter converts the maintained application SVG into
the ICO used by the executable and installer.

Run `build-windows.ps1` from PowerShell or `build-windows.sh` from MSYS2
UCRT64. The script does not download dependencies. It cleans only the
repository's `build/windows`, `build/pyinstaller`, `dist/Groovia` and
`dist/installer` outputs, compiles:

- `build/windows/groovia.gresource`
- `build/windows/schemas/gschemas.compiled`
- a GStreamer runtime subset and scanner discovered from `sys.prefix`

The final artifacts are `dist/Groovia/Groovia.exe` and
`dist/installer/Groovia-0.1.0-Setup.exe`. The installer defaults to
`%LOCALAPPDATA%\Programs\Groovia`, creates a Start Menu shortcut, offers an
unchecked desktop shortcut, and never removes user data during uninstall.

The application resolves resources through `groovia.runtime`, which supports
Meson installation, Flatpak, source/development runs and PyInstaller's
`sys._MEIPASS` without depending on the current directory. Windows data is
stored below `%LOCALAPPDATA%\Groovia`, configuration below `%APPDATA%\Groovia`,
and music below the user's Music folder.

For a source-tree development run after compiling the two generated files,
use the package module directly:

```powershell
$env:GROOVIA_RESOURCE_DIR = "$pwd\\build\\windows"
$env:GSETTINGS_SCHEMA_DIR = "$pwd\\build\\windows\\schemas"
python -m src.main
```

The MSYS2 UCRT64 shell must provide GTK, Libadwaita and GStreamer DLLs on its
PATH for this development mode.

Known limitations:

- MPRIS is intentionally unavailable on Windows because it is a Linux
  session-bus protocol.
- A packaged build does not create a Python virtual environment for spotDL;
  downloads require a separately provisioned compatible tool or a development
  build. Playback and library scanning do not depend on spotDL.
- Codec support follows the GStreamer plugins present in the build's MSYS2
  environment. A missing plugin is a playback/discovery limitation rather
  than a reason for application startup to fail.
