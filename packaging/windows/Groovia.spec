# Groovia.spec
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

from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
BUILD_ROOT = Path(os.environ.get("GROOVIA_WINDOWS_BUILD_DIR", ROOT / "build" / "windows"))
PACKAGE_ROOT = BUILD_ROOT / "package"
RESOURCE = BUILD_ROOT / "groovia.gresource"
COMPILED_SCHEMAS = BUILD_ROOT / "schemas" / "gschemas.compiled"
TOOLS_ROOT = BUILD_ROOT / "tools"
LICENSE_ROOT = BUILD_ROOT / "licenses"
ADWAITA_ROOT = Path(sys.prefix) / "share" / "icons" / "Adwaita"
HICOLOR_ROOT = Path(sys.prefix) / "share" / "icons" / "hicolor"
VERSION_FILE = ROOT / "VERSION"

try:
    APP_VERSION = VERSION_FILE.read_text(encoding="utf-8").strip()
except OSError as error:
    raise SystemExit(f"Missing {VERSION_FILE}; add the project VERSION file first") from error

VERSION_PARTS = APP_VERSION.split(".")
if len(VERSION_PARTS) != 3 or not all(part.isdigit() for part in VERSION_PARTS):
    raise SystemExit(
        f"Invalid Windows version {APP_VERSION!r} in {VERSION_FILE}; expected MAJOR.MINOR.PATCH"
    )

VERSION_INFO = BUILD_ROOT / "version_info.generated.txt"
VERSION_INFO.write_text(
    f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({VERSION_PARTS[0]}, {VERSION_PARTS[1]}, {VERSION_PARTS[2]}, 0),
    prodvers=({VERSION_PARTS[0]}, {VERSION_PARTS[1]}, {VERSION_PARTS[2]}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)
  ),
  kids=[StringFileInfo([
    StringTable('040904B0', [
      StringStruct('CompanyName', 'Lluciocc'),
      StringStruct('FileDescription', 'Groovia'),
      StringStruct('FileVersion', '{APP_VERSION}'),
      StringStruct('InternalName', 'Groovia'),
      StringStruct('OriginalFilename', 'Groovia.exe'),
      StringStruct('ProductName', 'Groovia'),
      StringStruct('ProductVersion', '{APP_VERSION}'),
      StringStruct('LegalCopyright', 'Copyright 2026 Lluciocc'),
    ])
  ]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)\n""",
    encoding="utf-8",
)

if not RESOURCE.is_file():
    raise SystemExit(f"Missing {RESOURCE}; run build-windows.ps1 first")
if not COMPILED_SCHEMAS.is_file():
    raise SystemExit(f"Missing {COMPILED_SCHEMAS}; run build-windows.ps1 first")
for required_tool in ("spotdl.exe", "ffmpeg.exe", "ffprobe.exe", "deno.exe"):
    if not (TOOLS_ROOT / required_tool).is_file():
        raise SystemExit(f"Missing staged tool {TOOLS_ROOT / required_tool}; run stage-dependencies.ps1 first")

if not ADWAITA_ROOT.is_dir():
    raise SystemExit(
        f"Missing Adwaita icon theme: {ADWAITA_ROOT}\n"
        "Install it in the MSYS2 UCRT64 environment with:\n"
        "pacman -S mingw-w64-ucrt-x86_64-adwaita-icon-theme"
    )
if not HICOLOR_ROOT.is_dir():
    raise SystemExit(
        f"Missing hicolor icon theme: {HICOLOR_ROOT}\n"
        "Install the MSYS2 UCRT64 icon themes with:\n"
        "pacman -S mingw-w64-ucrt-x86_64-adwaita-icon-theme"
    )


def collect_tree(root: Path, destination: str) -> list[tuple[str, str]]:
    """Collect every file while preserving a theme's directory structure."""
    entries: list[tuple[str, str]] = []
    for path in root.rglob("*"):
        if path.is_file():
            relative_parent = path.parent.relative_to(root)
            target = Path(destination) / relative_parent
            entries.append((str(path), str(target)))
    return entries


datas = [
    (str(VERSION_FILE), "."),
    (str(RESOURCE), "."),
    (str(COMPILED_SCHEMAS), "schemas"),
    (str(ROOT / "data" / "icons" / "hicolor" / "scalable" / "apps" / "io.github.Lluciocc.Groovia.svg"), "share/icons/hicolor/scalable/apps"),
    (str(ROOT / "data" / "icons" / "hicolor" / "symbolic" / "apps" / "io.github.Lluciocc.Groovia-symbolic.svg"), "share/icons/hicolor/symbolic/apps"),
    (str(ROOT / "data" / "icons" / "hicolor" / "scalable" / "apps" / "io.github.Lluciocc.Groovia.svg"), "share/icons/Adwaita/scalable/apps"),
    (str(ROOT / "data" / "icons" / "hicolor" / "symbolic" / "apps" / "io.github.Lluciocc.Groovia-symbolic.svg"), "share/icons/Adwaita/symbolic/apps"),
    (str(BUILD_ROOT / "io.github.Lluciocc.Groovia.png"), "share/icons/hicolor/128x128/apps"),
    (str(BUILD_ROOT / "io.github.Lluciocc.Groovia.png"), "share/icons/Adwaita/128x128/apps"),
]
# Keep the complete upstream themes, including index.theme, cursors and every
# symbolic/scalable/raster directory.  The project-owned icons above remain
# explicit entries so they are merged into the copied hicolor tree.
datas += collect_tree(ADWAITA_ROOT, "share/icons/Adwaita")
datas += collect_tree(HICOLOR_ROOT, "share/icons/hicolor")
for tool in ("spotdl.exe", "ffmpeg.exe", "ffprobe.exe", "deno.exe"):
    datas.append((str(TOOLS_ROOT / tool), "tools"))
for license_file in LICENSE_ROOT.glob("*"):
    if license_file.is_file():
        datas.append((str(license_file), "licenses"))
binaries = []


def add_file(path: Path, destination: str) -> None:
    if path.is_file():
        item = (str(path), destination)
        if item not in binaries:
            binaries.append(item)


# PyGObject's Python package does not contain the native typelib directory on
# all MSYS2 layouts. Collect all available typelibs from the UCRT64 prefix.
typelib_root = Path(sys.prefix) / "lib" / "girepository-1.0"

if not typelib_root.is_dir():
    raise SystemExit(f"Missing GObject typelib directory: {typelib_root}")

for typelib in typelib_root.glob("*.typelib"):
    add_file(typelib, "typelibs")


# GStreamer core and the plugin scanner are runtime files, not Python modules.
prefix_bin = Path(sys.prefix) / "bin"
prefix_lib = Path(sys.prefix) / "lib"
for pattern in (
    "libgst*.dll", "libglib*.dll", "libgobject*.dll", "libgio*.dll",
    "libgmodule*.dll", "liborc*.dll", "libgtk-4*.dll", "libadwaita*.dll",
    "libgdk-4*.dll", "libpango*.dll", "libcairo*.dll", "libgraphene*.dll",
    "libgdk_pixbuf*.dll", "libpangocairo*.dll", "libpangoft2*.dll",
    "libharfbuzz*.dll", "libfontconfig*.dll", "libfreetype*.dll",
    "libepoxy*.dll", "libffi*.dll", "libxml2*.dll", "zlib*.dll",
    "libpng*.dll", "libjpeg*.dll", "libgirepository*.dll",
    "libgcc*.dll", "libstdc++*.dll", "libwinpthread*.dll", "libintl*.dll",
    "libiconv*.dll", "libpcre2*.dll", "libzstd*.dll", "liblzma*.dll",
    "libbz2*.dll", "libSoundTouch*.dll", "SoundTouch*.dll",
    "librubberband*.dll", "rubberband*.dll",
):
    for path in prefix_bin.glob(pattern):
        add_file(path, ".")
for plugin_root in (prefix_lib / "gstreamer-1.0", prefix_lib / "gstreamer-1.0" / "plugins"):
    if plugin_root.is_dir():
        for path in plugin_root.glob("*.dll"):
            add_file(path, "gstreamer-1.0")
scanner = prefix_lib / "gstreamer-1.0" / "gst-plugin-scanner.exe"
if not scanner.is_file():
    scanner = Path(sys.prefix) / "libexec" / "gstreamer-1.0" / "gst-plugin-scanner.exe"
add_file(scanner, "gstreamer-1.0")


hiddenimports = [
    "cairo",
    "gi._gi_cairo",
    # Auto DJ is loaded through groovia.autodj.__getattr__, so PyInstaller's
    # static import analysis cannot reliably discover its service module.
    "groovia.autodj.service",
    "gi.repository.Adw", "gi.repository.Gdk", "gi.repository.GdkPixbuf",
    "gi.repository.Gio", "gi.repository.GLib", "gi.repository.GObject",
    "gi.repository.Gst", "gi.repository.GstPbutils", "gi.repository.Gtk",
    "gi.repository.Pango", "gi.repository.PangoCairo",
]
hiddenimports += collect_submodules("groovia")
hiddenimports += collect_submodules("numpy")
hiddenimports += collect_submodules("scipy")
datas += collect_data_files("gi", includes=["*.typelib"])
datas += collect_data_files("numpy")
datas += collect_data_files("scipy")
binaries += collect_dynamic_libs("gi")
binaries += collect_dynamic_libs("numpy")
binaries += collect_dynamic_libs("scipy")


a = Analysis(
    [str(ROOT / "packaging" / "windows" / "entrypoint.py")],
    runtime_hooks=[str(ROOT / "packaging" / "windows" / "runtime-hook.py"),],
    pathex=[str(PACKAGE_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "windows" / "hooks")],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Groovia",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=os.environ.get("GROOVIA_WINDOWS_CONSOLE", "0").lower() in {"1", "true", "yes", "on"},
    icon=(
        str(BUILD_ROOT / "Groovia.ico")
        if (BUILD_ROOT / "Groovia.ico").is_file()
        else None
    ),
    version=str(VERSION_INFO),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Groovia",
)
