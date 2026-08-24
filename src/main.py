# main.py
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

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk

from .platform_compat import supports_mpris
from .runtime import configure_icon_theme, initialize_runtime

initialize_runtime()

from .window import GrooviaWindow

if supports_mpris():
    from .mpris import MprisService
else:
    MprisService = None


def _default_version():
    version_file = Path(__file__).resolve().parents[1] / "VERSION"
    try:
        version = version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return "dev"
    return version or "dev"


DEFAULT_VERSION = _default_version()


def _about_version(version, platform=sys.platform):
    if platform.startswith("win"):
        suffix = ".windows"
    elif platform == "darwin":
        suffix = ".macos"
    else:
        return version
    return version if version.endswith(suffix) else f"{version}{suffix}"


class GrooviaApplication(Adw.Application):
    def __init__(self, version=DEFAULT_VERSION):
        self.version = version or DEFAULT_VERSION
        super().__init__(
            application_id="io.github.Lluciocc.Groovia",
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
        )
        self._quitting = False
        self.create_action("quit", self.on_quit, ["<primary>q"])
        self.create_action("about", self.on_about)
        self.create_action("preferences", self.on_preferences)
        self.create_action("shortcuts", self.on_shortcuts, ["<primary>question"])
        self.create_action(
            "import", lambda *_: self._window_action("_choose_folder"), ["<primary>o"]
        )
        self.create_action(
            "search", lambda *_: self._window_action("_focus_search"), ["<primary>f"]
        )
        self.create_action(
            "show-lyrics",
            lambda *_: self._window_action("_show_lyrics"),
            ["<primary>l"],
        )
        self.create_action("toggle-play", lambda *_: self._window_action("_toggle_play"))
        self.create_action("next", lambda *_: self._window_action("_next"), ["<primary>Right"])
        self.create_action(
            "previous", lambda *_: self._window_action("_previous"), ["<primary>Left"]
        )
        self.create_action("mute", lambda *_: self._window_action("_toggle_mute"))
        self.create_action("home", lambda *_: self._window_action("_show_home"), ["<primary>1"])
        self.create_action(
            "library", lambda *_: self._window_action("_show_library"), ["<primary>2"]
        )
        self.create_action("albums", lambda *_: self._window_action("_show_albums"), ["<primary>3"])
        self.create_action(
            "artists", lambda *_: self._window_action("_show_artists"), ["<primary>4"]
        )
        self.create_action("queue", lambda *_: self._window_action("_show_queue"), ["<primary>5"])
        self.create_action(
            "download", lambda *_: self._window_action("_download_url"), ["<primary>d"]
        )
        self.create_action(
            "toggle-menu",
            lambda *_: self._window_action("_toggle_main_menu"),
            ["<primary>m"],
        )
        self.create_action(
            "toggle-lyrics-mode",
            lambda *_: self._window_action("_toggle_lyrics_mode"),
            ["<primary>j"],
        )
        self.create_action(
            "fullscreen-view",
            lambda *_: self._window_action("_toggle_fullscreen_view"),
            ["F11"],
        )
        self.create_action(
            "fullscreen-vinyl",
            lambda *_: self._window_action("_open_vinyl_fullscreen"),
            ["<primary><shift>v"],
        )
        self.create_action(
            "fullscreen-lyrics",
            lambda *_: self._window_action("_open_lyrics_fullscreen"),
            ["<primary><shift>l"],
        )

    def do_activate(self):
        configure_icon_theme()
        win = self._main_window() or GrooviaWindow(application=self)
        if MprisService is not None and not hasattr(self, "mpris"):
            self.mpris = MprisService(win)
        win.present()

    def do_open(self, files, _n_files, _hint):
        self.activate()
        win = self._main_window()
        if win:
            win.open_paths([file.get_path() for file in files if file.get_path()])

    def on_about(self, *_args):
        about = Adw.AboutDialog(
            application_name="Groovia",
            application_icon="io.github.Lluciocc.Groovia",
            comments=(
                "Groovia is built for people who still keep and enjoy their own music collection.\n\n"
                "It combines a clean, album-focused library with a playful vinyl-inspired interface, while keeping your music local and under your control. There are no accounts or subscriptions: Groovia helps you rediscover the music you already own.\n\n"
                "Groovia is still growing, but the goal is simple: make listening to a local music library feel personal, modern and enjoyable again.\n\n"
            ),
            developer_name="Lluciocc",
            version=_about_version(self.version),
            developers=["Lluciocc"],
            copyright="© 2026 Lluciocc",
            license_type=Gtk.License.GPL_3_0_ONLY,
            website="https://github.com/Lluciocc/Groovia",
            issue_url="https://github.com/Lluciocc/Groovia/issues",
        )
        about.add_credit_section(
            "Data Providers",
            [
                "TheAudioDB https://www.theaudiodb.com/free_music_api",
                "Better Lyrics https://lyrics-api-docs.boidu.dev/",
                "LRCLIB https://lrclib.net/docs",
                (
                    "Spotify oEmbed "
                    "https://developer.spotify.com/documentation/embeds/tutorials/"
                    "using-the-oembed-api"
                ),
            ],
        )
        about.add_credit_section(
            "Tools and Audio Sources",
            [
                "spotDL https://spotdl.github.io/spotify-downloader/",
                "YouTube Music https://music.youtube.com/",
                "FFmpeg https://ffmpeg.org/documentation.html",
                "Deno https://docs.deno.com/",
            ],
        )
        about.add_link("License", "https://www.gnu.org/licenses/gpl-3.0.html")
        about.add_link("Donate", "https://buymeacoffee.com/lluciocc")
        about.present(self._main_window())

    def on_preferences(self, *_args):
        from .preferences import PreferencesWindow

        window = self._main_window()
        if window is not None:
            PreferencesWindow(window).present()

    def on_shortcuts(self, *_args):
        # PyGObject registers these newer Adwaita widget types lazily.  Touch
        # them before Gtk.Builder parses the resource so they are available to
        # the XML loader on all supported libadwaita versions.
        Adw.ShortcutsDialog
        Adw.ShortcutsSection
        Adw.ShortcutsItem
        builder = Gtk.Builder.new_from_resource("/io/github/Lluciocc/Groovia/shortcuts-dialog.ui")
        dialog = builder.get_object("shortcuts_dialog")
        dialog.present(self._main_window())

    def _window_action(self, method):
        window = self._main_window()
        if window and hasattr(window, method):
            getattr(window, method)()

    def _main_window(self):
        active = self.props.active_window
        if isinstance(active, GrooviaWindow):
            return active
        return next(
            (window for window in self.get_windows() if isinstance(window, GrooviaWindow)),
            None,
        )

    def on_quit(self, *_args):
        self._quitting = True
        window = self._main_window()
        if window is not None:
            window.close()
        self.quit()

    def do_shutdown(self):
        if getattr(self, "mpris", None) is not None:
            self.mpris.close()
        # PyGObject does not bind Gio's virtual shutdown method correctly
        # through super() here; call the parent implementation explicitly.
        Gio.Application.do_shutdown(self)

    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main(version=DEFAULT_VERSION):
    initialize_runtime()
    return GrooviaApplication(version).run(sys.argv)
