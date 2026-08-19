# entrypoint.py
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

from __future__ import annotations

import os
import re
import sys
import traceback
from pathlib import Path


def _bundle_root() -> Path:
    """Return the PyInstaller bundle data directory."""
    bundle_root = getattr(sys, "_MEIPASS", None)

    if bundle_root:
        return Path(bundle_root)

    return Path(sys.executable).resolve().parent


def _application_version() -> str:
    version_file = _bundle_root() / "VERSION"
    try:
        version = version_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError(f"Bundled VERSION file is missing: {version_file}") from error
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError(f"Invalid bundled application version: {version!r}")
    return version


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
            "A required GTK or GObject Introspection typelib is missing from the Groovia bundle."
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

        if "--smoke-test" in sys.argv[1:]:
            import gi
            import numpy
            import scipy
            from scipy import signal

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst

            Gst.init(None)
            tempo_factory = next(
                (
                    Gst.ElementFactory.find(name)
                    for name in ("rubberband", "pitch", "scaletempo")
                    if Gst.ElementFactory.find(name)
                ),
                None,
            )
            if tempo_factory is None:
                raise RuntimeError("No GStreamer pitch-preserving tempo element is bundled")
            transformed = signal.savgol_filter(numpy.arange(9, dtype=float), 5, 2)
            if transformed.shape != (9,):
                raise RuntimeError("SciPy DSP smoke test failed")
            plugin = tempo_factory.get_plugin()
            print(
                f"Groovia Auto DJ smoke test: numpy={numpy.__version__} scipy={scipy.__version__} "
                f"gstreamer={Gst.version_string()} tempo={tempo_factory.get_name()} "
                f"plugin={plugin.get_name() if plugin else 'unknown'} "
                f"filename={plugin.get_filename() if plugin else 'unknown'}"
            )
            return 0

        # GTK 4's DrawingArea callbacks hand PyGObject a Cairo context.  The
        # MSYS2 bindings require the PyCairo foreign converter to be loaded
        # explicitly before any GTK/PangoCairo drawing code is imported.
        import cairo as _cairo  # noqa: F401
        import gi

        gi.require_foreign("cairo")

        # Import only after GI_TYPELIB_PATH and DLL search directories are set.
        from groovia.runtime import initialize_runtime

        initialize_runtime()

        from groovia import main as groovia_main

        return int(groovia_main.main(_application_version()))
    except BaseException as error:
        _startup_error(error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
