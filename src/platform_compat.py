# platform_compat.py
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
import subprocess
import sys
import webbrowser
from pathlib import Path

IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"


def _env_path(name: str, fallback: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else fallback


def _windows_local_appdata() -> Path:
    user_profile = Path(os.environ.get("USERPROFILE", Path.home()))
    return _env_path(
        "LOCALAPPDATA",
        _env_path("APPDATA", user_profile / "AppData" / "Local"),
    )


def get_data_dir() -> Path:
    """Return Groovia's application data directory."""
    if IS_WINDOWS:
        return _windows_local_appdata() / "Groovia"
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / "Groovia"
    return _env_path("XDG_DATA_HOME", Path.home() / ".local" / "share") / "groovia"


def get_cache_dir() -> Path:
    """Return Groovia's disposable cache directory."""
    if IS_WINDOWS:
        return _windows_local_appdata() / "Groovia" / "cache"
    if IS_MACOS:
        return Path.home() / "Library" / "Caches" / "Groovia"
    return _env_path("XDG_CACHE_HOME", Path.home() / ".cache") / "groovia"


def get_config_dir() -> Path:
    """Return Groovia's configuration directory."""
    if IS_WINDOWS:
        return _env_path("APPDATA", _windows_local_appdata()) / "Groovia"
    if IS_MACOS:
        return Path.home() / "Library" / "Preferences" / "Groovia"
    return _env_path("XDG_CONFIG_HOME", Path.home() / ".config") / "groovia"


def get_music_dir() -> Path:
    """Return the user's Music directory, without Groovia's subdirectory."""
    if configured := os.environ.get("GROOVIA_MUSIC_DIR"):
        return Path(configured).expanduser().resolve()
    if IS_MACOS:
        return Path.home() / "Music"
    if configured := os.environ.get("XDG_MUSIC_DIR"):
        return Path(configured).expanduser()
    return Path.home() / "Music"


def open_folder(path: str | Path) -> None:
    """Open a folder using the platform's native file manager."""
    target = str(Path(path).expanduser().resolve())
    if IS_WINDOWS:
        os.startfile(target)  # type: ignore[attr-defined]
        return
    if IS_MACOS:
        subprocess.run(["open", target], check=False, **subprocess_window_kwargs())
        return
    webbrowser.open(Path(target).as_uri())


def show_item_in_file_manager(path: str | Path) -> bool:
    """Reveal a file in Explorer, Finder, or a freedesktop file manager.

    The Linux D-Bus import is intentionally kept inside the Linux-only branch:
    Finder must never cause a call to ``org.freedesktop.FileManager1``.
    """
    target = Path(path).expanduser().resolve()
    if IS_WINDOWS:
        # Preserve Groovia's established Explorer behavior: open the parent
        # folder instead of changing Windows selection semantics.
        try:
            os.startfile(  # type: ignore[attr-defined]
                str(target if target.is_dir() else target.parent)
            )
            return True
        except OSError:
            return False
    if IS_MACOS:
        command = ["open", str(target)] if target.is_dir() else ["open", "-R", str(target)]
        try:
            return subprocess.run(command, check=False).returncode == 0
        except OSError:
            return False
    if IS_LINUX:
        uri = target.as_uri()
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib

            proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,
                "org.freedesktop.FileManager1",
                "/org/freedesktop/FileManager1",
                "org.freedesktop.FileManager1",
                None,
            )
            proxy.call_sync(
                "ShowItems",
                GLib.Variant("(ass)", ([uri], "")),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            return True
        except Exception:
            fallback_uri = (target if target.is_dir() else target.parent).as_uri()
            try:
                Gio.AppInfo.launch_default_for_uri(fallback_uri, None)
                return True
            except Exception:
                return bool(webbrowser.open(fallback_uri))
    return bool(webbrowser.open(target.as_uri()))


def open_uri(uri: str) -> bool:
    """Open a URI with the platform's default handler."""
    return bool(webbrowser.open(uri))


def supports_mpris() -> bool:
    """MPRIS is intentionally limited to Linux session-bus environments."""
    return IS_LINUX


def media_backend_name() -> str | None:
    """Return the supported desktop media backend, ready for future ports."""
    return "mpris" if supports_mpris() else None


def get_managed_executable_name(name: str) -> str:
    """Return a tool name suitable for PATH and managed venv lookup."""
    if IS_WINDOWS and not name.lower().endswith(".exe"):
        return f"{name}.exe"
    return name


def subprocess_window_kwargs() -> dict:
    """Hide helper consoles on Windows while keeping POSIX calls unchanged."""
    if not IS_WINDOWS:
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


def iter_gtk_children(container):
    """Return a stable snapshot of a GTK 4 widget's direct children.

    PyGObject does not expose GTK containers as Python iterables on every
    platform, so callers must use GTK's sibling traversal API instead.
    """
    children = []
    child = container.get_first_child()
    while child is not None:
        children.append(child)
        child = child.get_next_sibling()
    return children
