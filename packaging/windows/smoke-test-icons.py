"""Verify GTK can resolve Groovia's bundled Windows icon theme."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


REQUIRED_ICONS = (
    "audio-x-generic-symbolic",
    "open-menu-symbolic",
    "list-add-symbolic",
    "folder-music-symbolic",
    "document-save-symbolic",
    "media-playback-start-symbolic",
    "go-previous-symbolic",
    "find-location-symbolic",
    "view-fullscreen-symbolic",
    "text-x-generic-symbolic",
    "system-search-symbolic",
    "document-open-symbolic",
    "image-x-generic-symbolic",
    "view-restore-symbolic",
    "media-playlist-shuffle-symbolic",
    "view-more-symbolic",
    "view-refresh-symbolic",
    "starred-symbolic",
    "view-list-symbolic",
    "audio-volume-high-symbolic",
    "media-skip-forward-symbolic",
    "media-skip-backward-symbolic",
    "media-playlist-repeat-symbolic",
    "media-playlist-repeat-song-symbolic",
    "edit-paste-symbolic",
    "media-playback-pause-symbolic",
    "applications-graphics-symbolic",
    "edit-delete-symbolic",
    "sidebar-show-symbolic",
    "go-home-symbolic",
)


def prepend_env_path(name: str, value: Path) -> None:
    entries = [entry for entry in os.environ.get(name, "").split(os.pathsep) if entry]
    entries = [entry for entry in entries if Path(entry).resolve() != value.resolve()]
    os.environ[name] = os.pathsep.join([str(value), *entries])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-root", required=True, type=Path)
    args = parser.parse_args()

    bundle_root = args.bundle_root.resolve()
    internal_root = bundle_root / "_internal"
    share_dir = internal_root / "share"
    icon_dir = share_dir / "icons"
    if not (icon_dir / "Adwaita" / "index.theme").is_file():
        print(f"[FAIL] missing bundled Adwaita metadata: {icon_dir / 'Adwaita' / 'index.theme'}")
        return 1
    if not (icon_dir / "hicolor" / "index.theme").is_file():
        print(f"[FAIL] missing bundled hicolor metadata: {icon_dir / 'hicolor' / 'index.theme'}")
        return 1

    os.environ["GI_TYPELIB_PATH"] = str(internal_root / "typelibs")
    os.environ["GSETTINGS_SCHEMA_DIR"] = str(internal_root / "schemas")
    prepend_env_path("XDG_DATA_DIRS", share_dir)
    os.environ["PATH"] = os.pathsep.join([str(internal_root), str(internal_root / "tools"), os.environ.get("PATH", "")])
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(internal_root))

    try:
        import gi

        gi.require_version("Gdk", "4.0")
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, Gtk

        Gtk.init()
        display = Gdk.Display.get_default()
        if display is None:
            print("[FAIL] GTK initialized without a default display")
            return 1
        settings = Gtk.Settings.get_for_display(display)
        settings.set_property("gtk-icon-theme-name", "Adwaita")
        theme = Gtk.IconTheme.get_for_display(display)
        theme.add_search_path(str(icon_dir))
        print(f"[PASS] GTK display initialized: {display.get_name()}")
        print(f"[PASS] bundled icon search path registered: {icon_dir}")
        missing = [name for name in REQUIRED_ICONS if not theme.has_icon(name)]
        for name in REQUIRED_ICONS:
            state = "PASS" if name not in missing else "FAIL"
            print(f"[{state}] {name}")
        if missing:
            print("[FAIL] missing required icons: " + ", ".join(missing))
            return 1
    except Exception:
        import traceback

        traceback.print_exc()
        return 1
    print(f"[PASS] all {len(REQUIRED_ICONS)} required icons resolve from the bundled themes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
