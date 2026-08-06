"""PyInstaller entry point for the Groovia package."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _bundle_root() -> Path:
    """Return the PyInstaller bundle data directory."""
    bundle_root = getattr(sys, "_MEIPASS", None)

    if bundle_root:
        return Path(bundle_root)

    return Path(sys.executable).resolve().parent


def _configure_frozen_environment() -> None:
    """
    Configure GTK, GObject Introspection, GSettings and GStreamer paths before
    importing PyGObject or any Groovia module that imports gi.repository.
    """
    bundle_root = _bundle_root()

    typelib_dir = bundle_root / "typelibs"
    if typelib_dir.is_dir():
        os.environ["GI_TYPELIB_PATH"] = str(typelib_dir)

    schema_dir = bundle_root / "schemas"
    if (schema_dir / "gschemas.compiled").is_file():
        os.environ["GSETTINGS_SCHEMA_DIR"] = str(schema_dir)

    plugin_dir = bundle_root / "gstreamer-1.0"
    if plugin_dir.is_dir():
        os.environ["GST_PLUGIN_PATH_1_0"] = str(plugin_dir)
        os.environ["GST_PLUGIN_PATH"] = str(plugin_dir)
        os.environ["GST_PLUGIN_SYSTEM_PATH_1_0"] = ""

        scanner = plugin_dir / "gst-plugin-scanner.exe"
        if scanner.is_file():
            os.environ["GST_PLUGIN_SCANNER"] = str(scanner)

    share_dir = bundle_root / "share"
    if share_dir.is_dir():
        os.environ["XDG_DATA_DIRS"] = str(share_dir)

    tools_dir = bundle_root / "tools"
    if tools_dir.is_dir():
        os.environ["GROOVIA_TOOLS_DIR"] = str(tools_dir)

        current_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{tools_dir}{os.pathsep}{current_path}"

    os.add_dll_directory(str(bundle_root))

    if tools_dir.is_dir():
        os.add_dll_directory(str(tools_dir))

    if plugin_dir.is_dir():
        os.add_dll_directory(str(plugin_dir))


def _startup_error(error: BaseException) -> None:
    message = str(error)
    lowered = message.lower()

    if "namespace gtk not available" in lowered or "typelib" in lowered:
        detail = (
            "A required GTK or GObject Introspection typelib is missing "
            "from the Groovia bundle."
        )
    elif "dll" in lowered or "could not be loaded" in lowered:
        detail = (
            "A required GTK, Libadwaita, GStreamer or runtime DLL is missing "
            "from the Groovia bundle."
        )
    elif "settings schema" in lowered:
        detail = "The Groovia GSettings schema could not be loaded."
    else:
        detail = "The GTK/Libadwaita runtime could not be initialized."

    traceback.print_exception(error, file=sys.stderr)
    text = f"Groovia could not start.\n\n{detail}\n\n{message}"

    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            text,
            "Groovia",
            0x10,
        )
    except Exception:
        print(text, file=sys.stderr)


def main() -> int:
    try:
        _configure_frozen_environment()

        # GTK 4's DrawingArea callbacks hand PyGObject a Cairo context.  The
        # MSYS2 bindings require the PyCairo foreign converter to be loaded
        # explicitly before any GTK/PangoCairo drawing code is imported.
        import gi
        import cairo

        gi.require_foreign("cairo")

        # Import only after GI_TYPELIB_PATH and DLL search directories are set.
        from groovia.runtime import initialize_runtime

        initialize_runtime()

        from groovia import main as groovia_main

        return int(groovia_main.main("0.1.0"))
    except BaseException as error:
        _startup_error(error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
