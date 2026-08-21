# runtime.py
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

import gettext
import locale
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .logging_utils import configure_logger
from .platform_compat import (
    IS_MACOS,
    IS_WINDOWS,
    get_cache_dir,
    get_config_dir,
    get_managed_executable_name,
)

LOGGER = logging.getLogger("groovia.runtime")
configure_logger(LOGGER, "Groovia runtime")
_RESOURCE_REGISTERED = False


class ToolPythonUnavailable(RuntimeError):
    """Raised when no real Python CLI is available for managed tools."""


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _tool_python_works(candidate: Path) -> bool:
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        return False
    try:
        version = subprocess.run(
            [str(candidate), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5,
            check=False,
        )
        with tempfile.TemporaryDirectory(prefix="groovia-python-check-") as directory:
            venv = subprocess.run(
                [str(candidate), "-m", "venv", directory],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
        return version.returncode == 0 and venv.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def get_python_interpreter_for_tools() -> Path:
    """Return a real Python CLI for venv/module subprocesses."""
    if not (IS_MACOS and is_frozen()):
        return Path(sys.executable)

    candidates: list[Path] = []
    if contents := macos_bundle_contents():
        resources = contents / "Resources"
        candidates.extend(
            resources / relative
            for relative in (
                Path("python") / "bin" / "python3",
                Path("python") / "bin" / f"python{sys.version_info.major}.{sys.version_info.minor}",
            )
        )
        framework_bin = (
            contents / "Frameworks" / "Python.framework" / "Versions" / "Current" / "bin"
        )
        candidates.extend(
            framework_bin / name
            for name in ("python3", f"python{sys.version_info.major}.{sys.version_info.minor}")
        )

    candidates.extend(
        Path(path) for path in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3")
    )
    if path_python := shutil.which("python3"):
        candidates.append(Path(path_python))

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.expanduser()
        if candidate in seen or str(candidate) in {"/usr/bin/python3", "/usr/bin/python"}:
            continue
        seen.add(candidate)
        if _tool_python_works(candidate):
            LOGGER.info("Tool Python selected: %s", candidate)
            return candidate
    raise ToolPythonUnavailable(
        "No Python runtime suitable for installing downloader dependencies was found."
    )


def macos_bundle_contents(executable: str | Path | None = None) -> Path | None:
    """Return ``*.app/Contents`` for a bundled executable, if applicable."""
    candidate = Path(executable or sys.executable).expanduser().resolve()
    for parent in (candidate, *candidate.parents):
        if parent.name == "Contents" and parent.parent.suffix == ".app":
            return parent
    return None


def is_standalone_bundle(executable: str | Path | None = None) -> bool:
    """Whether strict, bundle-only dependency paths are appropriate."""
    return is_frozen() or bool(IS_MACOS and macos_bundle_contents(executable))


def _resource_roots(resource_dir: str | Path | None = None) -> list[Path]:
    roots: list[Path] = []
    if resource_dir:
        roots.append(Path(resource_dir))
    if contents := macos_bundle_contents():
        roots.append(contents / "Resources")
    if bundle_root := getattr(sys, "_MEIPASS", None):
        roots.append(Path(bundle_root))
    if configured := os.environ.get("GROOVIA_RESOURCE_DIR"):
        roots.append(Path(configured))
    module_dir = Path(__file__).resolve().parent
    roots.extend((module_dir, module_dir.parent))
    unique: list[Path] = []
    for root in roots:
        root = root.resolve()
        if root not in unique:
            unique.append(root)
    return unique


def bundled_resource_path(relative_path: str | Path, resource_dir=None) -> Path:
    """Resolve bundled data without relying on the current working directory."""
    relative = Path(relative_path)
    roots = _resource_roots(resource_dir)
    for root in roots:
        candidate = root / relative
        if candidate.exists():
            return candidate
    return roots[0] / relative


def bundled_tool_path(name: str, tools_dir: str | Path | None = None) -> Path | None:
    """Resolve a native downloader tool in a frozen bundle or dev staging dir."""
    executable = get_managed_executable_name(name)
    roots: list[Path] = []
    if tools_dir:
        roots.append(Path(tools_dir))
    if configured := os.environ.get("GROOVIA_TOOLS_DIR"):
        roots.append(Path(configured))
    if is_standalone_bundle() or IS_WINDOWS or IS_MACOS:
        roots.append(bundled_resource_path("tools"))
    for root in roots:
        candidate = root / executable
        if candidate.is_file():
            return candidate
    return None


def _set_if_directory(name: str, path: Path) -> None:
    if path.is_dir():
        _prepend_path_environment(name, path)


def _prepend_path_environment(name: str, path: Path) -> None:
    """Prepend a bundled search root without discarding user configuration."""
    value = str(path)
    entries = [entry for entry in os.environ.get(name, "").split(os.pathsep) if entry]
    entries = [entry for entry in entries if Path(entry).resolve() != path.resolve()]
    os.environ[name] = os.pathsep.join([value, *entries])


def gstreamer_scanner_name() -> str:
    return get_managed_executable_name("gst-plugin-scanner")


def find_gstreamer_scanner(resource_dir=None) -> Path | None:
    """Find the scanner in bundle layouts used by GStreamer and Homebrew."""
    scanner = gstreamer_scanner_name()
    candidates = [
        bundled_resource_path(Path("gstreamer-1.0") / scanner, resource_dir),
        bundled_resource_path(Path("libexec/gstreamer-1.0") / scanner, resource_dir),
        bundled_resource_path(Path("tools") / scanner, resource_dir),
    ]
    if contents := macos_bundle_contents():
        candidates.extend(
            (
                contents / "Frameworks" / "libexec" / "gstreamer-1.0" / scanner,
                contents / "Resources" / "libexec" / "gstreamer-1.0" / scanner,
            )
        )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def tool_process_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """Return a child-process environment with private bundled tools."""
    environment = dict(os.environ if base is None else base)
    tools = bundled_resource_path("tools")
    if tools.is_dir():
        entries = [str(tools), *filter(None, environment.get("PATH", "").split(os.pathsep))]
        environment["PATH"] = os.pathsep.join(dict.fromkeys(entries))
        for name, variable in (
            ("ffmpeg", "FFMPEG_BINARY"),
            ("ffprobe", "FFPROBE_BINARY"),
            ("deno", "DENO_BINARY"),
        ):
            if tool := bundled_tool_path(name, tools):
                environment[variable] = str(tool)
    return environment


def _configure_bundle_environment(resource_dir=None) -> None:
    standalone = is_standalone_bundle()
    if IS_WINDOWS:
        # The keyfile backend is portable and keeps preferences in the user's
        # profile instead of requiring a system-wide registry installation.
        os.environ.setdefault("GSETTINGS_BACKEND", "keyfile")
        os.environ.setdefault("XDG_CONFIG_HOME", str(get_config_dir()))
    elif IS_MACOS and standalone:
        # Homebrew GLib does not guarantee a dconf service on end-user Macs.
        # The keyfile remains inside Groovia's macOS Preferences directory.
        os.environ.setdefault("GSETTINGS_BACKEND", "keyfile")
        os.environ.setdefault("XDG_CONFIG_HOME", str(get_config_dir()))

    schema_dir = bundled_resource_path("schemas", resource_dir)
    if (schema_dir / "gschemas.compiled").is_file():
        if standalone:
            os.environ["GSETTINGS_SCHEMA_DIR"] = str(schema_dir)
        else:
            os.environ.setdefault("GSETTINGS_SCHEMA_DIR", str(schema_dir))
    elif standalone:
        LOGGER.error("GSettings schema bundle is missing: %s", schema_dir)

    typelib_dir = bundled_resource_path("typelibs", resource_dir)
    if typelib_dir.is_dir() and standalone:
        os.environ["GI_TYPELIB_PATH"] = str(typelib_dir)
    else:
        _set_if_directory("GI_TYPELIB_PATH", typelib_dir)
    if standalone and not typelib_dir.is_dir():
        LOGGER.error("GObject typelibs are missing: %s", typelib_dir)

    plugin_dir = bundled_resource_path("gstreamer-1.0", resource_dir)
    if plugin_dir.is_dir():
        if standalone:
            os.environ["GST_PLUGIN_PATH_1_0"] = str(plugin_dir)
            os.environ["GST_PLUGIN_PATH"] = str(plugin_dir)
            os.environ["GST_PLUGIN_SYSTEM_PATH_1_0"] = ""
        else:
            _prepend_path_environment("GST_PLUGIN_PATH_1_0", plugin_dir)
            _prepend_path_environment("GST_PLUGIN_PATH", plugin_dir)
        if scanner := find_gstreamer_scanner(resource_dir):
            os.environ["GST_PLUGIN_SCANNER"] = str(scanner)
        elif standalone:
            LOGGER.error("GStreamer plugin scanner is missing below %s", plugin_dir)
        try:
            get_cache_dir().mkdir(parents=True, exist_ok=True)
            registry = str(get_cache_dir() / "gstreamer-registry.bin")
            if standalone:
                os.environ["GST_REGISTRY_1_0"] = registry
            else:
                os.environ.setdefault("GST_REGISTRY_1_0", registry)
        except OSError:
            LOGGER.info("Could not create a writable GStreamer registry cache")
    elif standalone:
        LOGGER.error("GStreamer plugin directory is missing: %s", plugin_dir)

    share_dir = bundled_resource_path("share", resource_dir)
    if share_dir.is_dir():
        if standalone:
            os.environ["XDG_DATA_DIRS"] = str(share_dir)
        else:
            _prepend_path_environment("XDG_DATA_DIRS", share_dir)


def configure_icon_theme(resource_dir=None) -> None:
    """Register the bundled Adwaita theme for a frozen Windows/macOS process.

    Linux and Flatpak retain their normal system-selected icon theme.  GTK's
    display-dependent API is intentionally called separately from the early
    environment setup, after an application has a display but before its
    first window is constructed.
    """
    if not ((IS_WINDOWS or IS_MACOS) and is_standalone_bundle()):
        return

    share_dir = bundled_resource_path("share", resource_dir)
    icon_dir = share_dir / "icons"
    if not icon_dir.is_dir():
        LOGGER.error("Bundled icon theme directory is missing: %s", icon_dir)
        return

    _prepend_path_environment("XDG_DATA_DIRS", share_dir)
    try:
        import gi

        gi.require_version("Gdk", "4.0")
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, Gtk

        display = Gdk.Display.get_default()
        if display is None:
            LOGGER.warning("Cannot configure bundled icon theme before a GDK display exists")
            return
        # GTK 4 exposes the display icon theme as a singleton, on which
        # set_theme_name() raises a critical assertion.  Set the supported
        # display setting first; the singleton then selects Adwaita normally.
        settings = Gtk.Settings.get_for_display(display)
        settings.set_property("gtk-icon-theme-name", "Adwaita")
        theme = Gtk.IconTheme.get_for_display(display)
        theme.add_search_path(str(icon_dir))
    except Exception:
        LOGGER.exception("Could not configure the bundled Adwaita icon theme")


def _register_gresource(resource_dir=None) -> None:
    global _RESOURCE_REGISTERED
    if _RESOURCE_REGISTERED:
        return
    path = bundled_resource_path("groovia.gresource", resource_dir)
    if not path.is_file():
        LOGGER.warning("Groovia resource bundle is missing: %s", path)
        return
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        Gio.Resource.load(str(path))._register()
        _RESOURCE_REGISTERED = True
    except Exception:
        LOGGER.exception("Could not register Groovia resource bundle: %s", path)


def _configure_translations(localedir=None) -> None:
    directory = Path(localedir) if localedir else bundled_resource_path("locale")
    # Keep the Linux locale setup order used by the original launcher.  The
    # locale module's POSIX helpers are not guaranteed to exist on Windows.
    for name, args in (
        ("bindtextdomain", ("groovia", str(directory))),
        ("textdomain", ("groovia",)),
    ):
        function = getattr(locale, name, None)
        if function:
            try:
                function(*args)
            except (OSError, RuntimeError):
                LOGGER.info("locale.%s is unavailable", name)
    try:
        gettext.bindtextdomain("groovia", str(directory))
        gettext.textdomain("groovia")
    except (AttributeError, OSError):
        LOGGER.info("gettext domain setup is unavailable; using untranslated strings")


def initialize_runtime(resource_dir=None, localedir=None) -> None:
    """Configure data discovery before GTK widgets or GSettings are created."""
    _configure_bundle_environment(resource_dir)
    _configure_translations(localedir)
    _register_gresource(resource_dir)


def create_settings(schema_id: str = "io.github.Lluciocc.Groovia"):
    """Create Gio.Settings with an actionable missing-schema diagnostic."""
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(schema_id, True) if source else None
    if schema is None:
        configured = os.environ.get("GSETTINGS_SCHEMA_DIR", "<system search path>")
        message = (
            f"GSettings schema '{schema_id}' is unavailable. Searched schema directory: "
            f"{configured}. Compile data/{schema_id}.gschema.xml with glib-compile-schemas "
            "or rebuild the Groovia bundle."
        )
        LOGGER.error(message)
        raise RuntimeError(message)
    return Gio.Settings.new_full(schema, None, None)
