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
import sys
from pathlib import Path

from .logging_utils import configure_logger
from .platform_compat import IS_WINDOWS, get_cache_dir, get_config_dir, get_managed_executable_name

LOGGER = logging.getLogger("groovia.runtime")
configure_logger(LOGGER, "Groovia runtime")
_RESOURCE_REGISTERED = False


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _resource_roots(resource_dir: str | Path | None = None) -> list[Path]:
    roots: list[Path] = []
    if resource_dir:
        roots.append(Path(resource_dir))
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
    if is_frozen() or IS_WINDOWS:
        roots.append(bundled_resource_path("tools"))
    for root in roots:
        candidate = root / executable
        if candidate.is_file():
            return candidate
    return None


def _set_if_directory(name: str, path: Path) -> None:
    if path.is_dir():
        os.environ.setdefault(name, str(path))


def _prepend_path_environment(name: str, path: Path) -> None:
    """Prepend a bundled search root without discarding user configuration."""
    value = str(path)
    entries = [entry for entry in os.environ.get(name, "").split(os.pathsep) if entry]
    entries = [entry for entry in entries if Path(entry).resolve() != path.resolve()]
    os.environ[name] = os.pathsep.join([value, *entries])


def _configure_bundle_environment(resource_dir=None) -> None:
    if IS_WINDOWS:
        # The keyfile backend is portable and keeps preferences in the user's
        # profile instead of requiring a system-wide registry installation.
        os.environ.setdefault("GSETTINGS_BACKEND", "keyfile")
        os.environ.setdefault("XDG_CONFIG_HOME", str(get_config_dir()))

    schema_dir = bundled_resource_path("schemas", resource_dir)
    if (schema_dir / "gschemas.compiled").is_file():
        os.environ.setdefault("GSETTINGS_SCHEMA_DIR", str(schema_dir))
    elif getattr(sys, "frozen", False):
        LOGGER.error("GSettings schema bundle is missing: %s", schema_dir)

    typelib_dir = bundled_resource_path("typelibs", resource_dir)
    _set_if_directory("GI_TYPELIB_PATH", typelib_dir)
    if getattr(sys, "frozen", False) and not typelib_dir.is_dir():
        LOGGER.error("GObject typelibs are missing: %s", typelib_dir)

    plugin_dir = bundled_resource_path("gstreamer-1.0", resource_dir)
    if plugin_dir.is_dir():
        os.environ.setdefault("GST_PLUGIN_PATH_1_0", str(plugin_dir))
        os.environ.setdefault("GST_PLUGIN_PATH", str(plugin_dir))
        os.environ.setdefault("GST_PLUGIN_SYSTEM_PATH_1_0", "")
        scanner = bundled_resource_path("gstreamer-1.0/gst-plugin-scanner.exe", resource_dir)
        if scanner.is_file():
            os.environ.setdefault("GST_PLUGIN_SCANNER", str(scanner))
        elif getattr(sys, "frozen", False):
            LOGGER.error("GStreamer plugin scanner is missing: %s", scanner)
        try:
            get_cache_dir().mkdir(parents=True, exist_ok=True)
            os.environ.setdefault(
                "GST_REGISTRY_1_0", str(get_cache_dir() / "gstreamer-registry.bin")
            )
        except OSError:
            LOGGER.info("Could not create a writable GStreamer registry cache")
    elif getattr(sys, "frozen", False):
        LOGGER.error("GStreamer plugin directory is missing: %s", plugin_dir)

    share_dir = bundled_resource_path("share", resource_dir)
    if share_dir.is_dir():
        if IS_WINDOWS and is_frozen():
            _prepend_path_environment("XDG_DATA_DIRS", share_dir)
        else:
            os.environ.setdefault("XDG_DATA_DIRS", str(share_dir))


def configure_icon_theme(resource_dir=None) -> None:
    """Register the bundled Adwaita theme for a frozen Windows process.

    Linux and Flatpak retain their normal system-selected icon theme.  GTK's
    display-dependent API is intentionally called separately from the early
    environment setup, after an application has a display but before its
    first window is constructed.
    """
    if not (IS_WINDOWS and is_frozen()):
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
