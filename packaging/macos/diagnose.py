#!/usr/bin/env python3

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.platform_compat import get_cache_dir, get_config_dir, get_data_dir, get_music_dir
from src.runtime import bundled_tool_path, find_gstreamer_scanner, initialize_runtime

REQUIRED_GST = ("playbin", "audioconvert", "audioresample")
OPTIONAL_GST = ("equalizer-3bands", "audioecho", "freeverb", "rubberband", "pitch", "scaletempo")


def report(state: str, label: str, value: object) -> None:
    print(f"[{state:<4}] {label}: {value}")


def command_version(name: str, argument: str) -> tuple[bool, str]:
    path = bundled_tool_path(name) or shutil.which(name)
    if not path and name == "spotdl":
        private = get_data_dir() / "downloader" / "venv" / "bin" / "spotdl"
        path = private if private.is_file() else None
    if not path:
        return False, "not found"
    try:
        result = subprocess.run(
            [str(path), argument], capture_output=True, text=True, timeout=10, check=False
        )
        first = (result.stdout or result.stderr or "").strip().splitlines()
        return result.returncode == 0, f"{path} — {first[0] if first else 'no version output'}"
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)


def main() -> int:
    initialize_runtime(os.environ.get("GROOVIA_RESOURCE_DIR"))
    failures: list[str] = []
    report("INFO", "architecture", f"{platform.machine()} ({platform.platform()})")
    report("OK", "Python", sys.version.replace("\n", " "))
    report("INFO", "executable", sys.executable)
    report("INFO", "GI_TYPELIB_PATH", os.environ.get("GI_TYPELIB_PATH", "<system>"))
    report("INFO", "GSETTINGS_SCHEMA_DIR", os.environ.get("GSETTINGS_SCHEMA_DIR", "<system>"))
    report("INFO", "GST_PLUGIN_PATH_1_0", os.environ.get("GST_PLUGIN_PATH_1_0", "<system>"))
    report("INFO", "GST_PLUGIN_SCANNER", find_gstreamer_scanner() or "<system discovery>")

    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        gi.require_version("Gst", "1.0")
        from gi.repository import Adw, Gst, Gtk

        report(
            "OK",
            "GTK",
            f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}",
        )
        report(
            "OK",
            "Libadwaita",
            f"{Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}",
        )
        Gst.init(None)
        report("OK", "GStreamer", Gst.version_string())
        for name in (*REQUIRED_GST, *OPTIONAL_GST):
            available = Gst.ElementFactory.find(name) is not None
            required = name in REQUIRED_GST
            report(
                "OK" if available else ("FAIL" if required else "WARN"),
                f"GStreamer {name}",
                available,
            )
            if required and not available:
                failures.append(f"GStreamer element {name}")
    except Exception as error:
        report("FAIL", "GTK/Libadwaita/GStreamer", error)
        failures.append("PyGObject GTK/Libadwaita/GStreamer runtime")

    for module in ("numpy", "scipy"):
        try:
            imported = __import__(module)
            report("OK", module, imported.__version__)
        except Exception as error:
            report("FAIL", module, error)
            failures.append(module)

    try:
        from src.runtime import create_settings

        create_settings()
        report("OK", "GSettings schema", "io.github.Lluciocc.Groovia")
    except Exception as error:
        report("FAIL", "GSettings schema", error)
        failures.append("GSettings schema")

    for name, argument in (
        ("ffmpeg", "-version"),
        ("ffprobe", "-version"),
        ("spotdl", "--version"),
        ("deno", "--version"),
    ):
        available, value = command_version(name, argument)
        report("OK" if available else "WARN", name, value)
    for label, path in (
        ("data", get_data_dir()),
        ("cache", get_cache_dir()),
        ("configuration", get_config_dir()),
        ("music", get_music_dir()),
    ):
        report("INFO", f"Groovia {label}", path)
    if platform.system() == "Darwin" and platform.machine() != "arm64":
        report("WARN", "official target", "first official target is arm64; this host is not arm64")
    if failures:
        report("FAIL", "mandatory dependencies", ", ".join(failures))
        return 1
    report("OK", "mandatory dependencies", "launch and basic playback requirements found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
