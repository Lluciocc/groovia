from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
BUILD_ROOT = Path(os.environ.get("GROOVIA_MACOS_BUILD_DIR", ROOT / "build" / "macos"))
PACKAGE_ROOT = BUILD_ROOT / "package"
RESOURCE_ROOT = BUILD_ROOT / "stage" / "Resources"
PLIST = BUILD_ROOT / "stage" / "Info.plist"
ARCH = os.environ.get("GROOVIA_MACOS_ARCH", "arm64")
sys.path.insert(0, str(PACKAGE_ROOT))

if ARCH not in {"arm64", "x86_64", "universal2"}:
    raise SystemExit(f"Unsupported GROOVIA_MACOS_ARCH={ARCH!r}")
for required in (
    RESOURCE_ROOT / "groovia.gresource",
    RESOURCE_ROOT / "VERSION",
    RESOURCE_ROOT / "schemas" / "gschemas.compiled",
    RESOURCE_ROOT / "Groovia.icns",
    PLIST,
):
    if not required.is_file():
        raise SystemExit(f"Missing staged macOS resource: {required}")


def collect_tree(root: Path, destination: str) -> list[tuple[str, str]]:
    return [
        (str(path), str(Path(destination) / path.parent.relative_to(root)))
        for path in root.rglob("*")
        if path.is_file()
        and path.parent.relative_to(root).parts[:1] not in {("gstreamer-1.0",), ("libexec",)}
    ]


datas = collect_tree(RESOURCE_ROOT, ".")
binaries = []
for path in (RESOURCE_ROOT / "gstreamer-1.0").iterdir():
    if path.is_file() and path.suffix in {".dylib", ".so"}:
        binaries.append((str(path), "gstreamer-1.0"))
scanner = RESOURCE_ROOT / "libexec" / "gstreamer-1.0" / "gst-plugin-scanner"
if scanner.is_file():
    binaries.append((str(scanner), "libexec/gstreamer-1.0"))

hiddenimports = [
    "cairo",
    "gi._gi_cairo",
    "groovia.autodj.service",
    "gi.repository.Adw",
    "gi.repository.Gdk",
    "gi.repository.GdkPixbuf",
    "gi.repository.Gio",
    "gi.repository.GLib",
    "gi.repository.GObject",
    "gi.repository.Gst",
    "gi.repository.GstPbutils",
    "gi.repository.Gtk",
    "gi.repository.Pango",
    "gi.repository.PangoCairo",
]
hiddenimports += collect_submodules("groovia")
hiddenimports += collect_submodules("numpy")
hiddenimports += collect_submodules("scipy")
datas += collect_data_files("numpy")
datas += collect_data_files("scipy")
binaries += collect_dynamic_libs("gi")
binaries += collect_dynamic_libs("numpy")
binaries += collect_dynamic_libs("scipy")

with PLIST.open("rb") as stream:
    info_plist = plistlib.load(stream)

a = Analysis(
    [str(ROOT / "packaging" / "macos" / "entrypoint.py")],
    pathex=[str(PACKAGE_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "macos" / "hooks")],
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
    console=False,
    target_arch=ARCH,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Groovia")
app = BUNDLE(
    coll,
    name="Groovia.app",
    icon=str(RESOURCE_ROOT / "Groovia.icns"),
    bundle_identifier="io.github.Lluciocc.Groovia",
    info_plist=info_plist,
    target_arch=ARCH,
)
