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
  mingw-w64-ucrt-x86_64-glib2 mingw-w64-ucrt-x86_64-python-pip \
  mingw-w64-ucrt-x86_64-curl
```

Install PyInstaller explicitly into the UCRT64 Python with
`python -m pip install pyinstaller`. This Python and pip are build-machine
requirements only; they are never needed by an installed application. The
packaging scripts download only the exact URLs and SHA-256-pinned artifacts in
`dependencies.json`, or reuse their verified cache. They never request an
unpinned latest release.

The Windows host also needs Inno Setup (`iscc.exe`) and ImageMagick
(`magick`) on PATH. The latter converts the maintained application SVG into
the ICO used by the executable and installer.

Run `build-windows.ps1` from PowerShell or `build-windows.sh` from MSYS2
UCRT64. The build is a GUI application by default; pass `-Console` to the
PowerShell script (or `--console` to the Bash wrapper) to keep a console
attached to the executable. The `dist` directory is opened in Explorer after a
successful build by default; pass `-DontOpenDist` (or `--dont-open-dist`) to
disable it. It stages only the pinned downloader artifacts from
`dependencies.json`, then cleans only the
repository's `build/windows`, `build/pyinstaller`, `dist/Groovia` and
`dist/installer` outputs, compiles:

- `build/windows/groovia.gresource`
- `build/windows/schemas/gschemas.compiled`
- `build/windows/tools/{spotdl.exe,ffmpeg.exe,ffprobe.exe,deno.exe}`
- `build/windows/licenses/` with the upstream license files
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

The final package keeps the tools private to the installation:
`{app}\tools\spotdl.exe`, `{app}\tools\ffmpeg.exe`,
`{app}\tools\ffprobe.exe` and `{app}\tools\deno.exe`. The smoke test runs
`spotdl.exe --version`, `ffmpeg.exe -version` and `deno.exe --version` with an
explicit working directory and captured output. Use
`stage-dependencies.ps1 -Offline` to require a previously verified artifact
cache.

The pinned sources currently are spotDL 4.5.2 from the official spotDL
release, Deno 2.9.4 from the official Deno release, and the dated win64 GPL
FFmpeg build from BtbN. `dependencies.json` contains their exact URLs and
hashes; update that file and rerun staging to upgrade them. FFmpeg's upstream
GPL obligations and all fetched license files are preserved in the package.

Current artifact checksums:

- spotDL 4.5.2: `4490AE3B38C4321173E17975A9990A130CF9A9AEA8132EE2978AFECEFBEEB477`
- FFmpeg `N-125978-g95c43d7df7`: `ee299bc305e69ebec53c7eb4419a0397df12aaebb9f3c51552ab937248ba9fa9`
- Deno 2.9.4: `68ED08B05C56CF887E9AA509947DC3F468F7E12F47A13E5C1ABD51D46D1453EF`

The package includes spotDL's `LICENSE`, Deno's `LICENSE.md`, and the
FFmpeg-build license files under `licenses/`. When updating a dependency,
verify the release asset and license from the upstream source, update both
hashes in `dependencies.json`, and run the online staging step followed by
the offline staging/smoke test.

Known limitations:

- MPRIS is intentionally unavailable on Windows because it is a Linux
  session-bus protocol.
- A packaged Windows build never creates a virtual environment or calls pip;
  the installer owns its staged downloader binaries.
- Codec support follows the GStreamer plugins present in the build's MSYS2
  environment. A missing plugin is a playback/discovery limitation rather
  than a reason for application startup to fail.
- The pinned spotDL/FFmpeg/Deno artifacts must be refreshed deliberately when
  upstream versions change; the build does not silently track latest releases.
