"""One-folder Windows build for Groovia under MSYS2 UCRT64.

The build script creates the compiled resource and schema files first.  This
spec deliberately collects only the GStreamer runtime/plugin directories and
the typelibs used by Groovia; it does not copy an entire MSYS2 installation.
"""

from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


ROOT = Path(__file__).resolve().parents[2]
BUILD_ROOT = Path(os.environ.get("GROOVIA_WINDOWS_BUILD_DIR", ROOT / "build" / "windows"))
PACKAGE_ROOT = BUILD_ROOT / "package"
RESOURCE = BUILD_ROOT / "groovia.gresource"
COMPILED_SCHEMAS = BUILD_ROOT / "schemas" / "gschemas.compiled"

if not RESOURCE.is_file():
    raise SystemExit(f"Missing {RESOURCE}; run build-windows.ps1 first")
if not COMPILED_SCHEMAS.is_file():
    raise SystemExit(f"Missing {COMPILED_SCHEMAS}; run build-windows.ps1 first")


datas = [
    (str(RESOURCE), "."),
    (str(COMPILED_SCHEMAS), "schemas"),
    (str(ROOT / "data" / "icons" / "hicolor" / "scalable" / "apps" / "io.github.Lluciocc.Groovia.svg"), "share/icons/hicolor/scalable/apps"),
    (str(ROOT / "data" / "icons" / "hicolor" / "symbolic" / "apps" / "io.github.Lluciocc.Groovia-symbolic.svg"), "share/icons/hicolor/symbolic/apps"),
]
binaries = []


def add_file(path: Path, destination: str) -> None:
    if path.is_file():
        item = (str(path), destination)
        if item not in binaries:
            binaries.append(item)


# PyGObject's Python package does not contain the native typelib directory on
# all MSYS2 layouts.  Collect the small typelib set explicitly when present.
typelib_roots = [Path(sys.prefix) / "lib" / "girepository-1.0", Path(sys.prefix) / "share" / "gir-1.0"]
typelib_names = {
    "Adw-1.typelib", "Gdk-4.0.typelib", "GdkPixbuf-2.0.typelib",
    "Gio-2.0.typelib", "GLib-2.0.typelib", "GObject-2.0.typelib",
    "Gst-1.0.typelib", "GstApp-1.0.typelib", "GstAudio-1.0.typelib",
    "GstBase-1.0.typelib", "GstPbutils-1.0.typelib", "GstTag-1.0.typelib",
    "GstVideo-1.0.typelib", "Gtk-4.0.typelib",
    "Pango-1.0.typelib", "PangoCairo-1.0.typelib", "cairo-1.0.typelib",
    "Graphene-1.0.typelib",
}
for root in typelib_roots:
    for name in typelib_names:
        add_file(root / name, "typelibs")


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
    "libbz2*.dll",
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
    "gi.repository.Adw", "gi.repository.Gdk", "gi.repository.GdkPixbuf",
    "gi.repository.Gio", "gi.repository.GLib", "gi.repository.GObject",
    "gi.repository.Gst", "gi.repository.GstPbutils", "gi.repository.Gtk",
    "gi.repository.Pango", "gi.repository.PangoCairo",
]
hiddenimports += collect_submodules("groovia")
datas += collect_data_files("gi", includes=["*.typelib"])
binaries += collect_dynamic_libs("gi")


a = Analysis(
    [str(ROOT / "packaging" / "windows" / "entrypoint.py")],
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
    a.binaries,
    a.datas,
    [],
    name="Groovia",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(BUILD_ROOT / "Groovia.ico") if (BUILD_ROOT / "Groovia.ico").is_file() else None,
    version=str(ROOT / "packaging" / "windows" / "version_info.txt") if (ROOT / "packaging" / "windows" / "version_info.txt").is_file() else None,
)
