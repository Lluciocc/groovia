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

DEFAULT_VERSION = "1.1.1"


class GrooviaApplication(Adw.Application):
    def __init__(self, version=DEFAULT_VERSION):
        self.version = version or DEFAULT_VERSION
        super().__init__(
            application_id="io.github.Lluciocc.Groovia",
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
        )
        self.create_action("quit", lambda *_: self.quit(), ["<primary>q"])
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

    def do_activate(self):
        configure_icon_theme()
        win = self.props.active_window or GrooviaWindow(application=self)
        if MprisService is not None and not hasattr(self, "mpris"):
            self.mpris = MprisService(win)
        win.present()

    def do_open(self, files, _n_files, _hint):
        self.activate()
        win = self.props.active_window
        if win:
            win.open_paths([file.get_path() for file in files if file.get_path()])

    def on_about(self, *_args):
        about = Adw.AboutDialog(
            application_name="Groovia",
            application_icon="io.github.Lluciocc.Groovia",
            comments=(
                "Modern, album-first music player for GNOME.\n\n"
                "Build a local music collection, browse albums, shape the queue, "
                "follow synchronized lyrics, and let the optional Auto DJ create "
                "thoughtful transitions between tracks."
            ),
            developer_name="Lluciocc",
            version=self.version,
            developers=["Lluciocc"],
            copyright="© 2026 Lluciocc",
            license_type=Gtk.License.GPL_3_0_ONLY,
            website="https://github.com/Lluciocc/Groovia",
            issue_url="https://github.com/Lluciocc/Groovia/issues",
        )
        about.add_link("License", "https://www.gnu.org/licenses/gpl-3.0.html")
        about.add_link("Donate", "https://buymeacoffee.com/lluciocc")
        about.present(self.props.active_window)

    def on_preferences(self, *_args):
        from .preferences import PreferencesWindow

        PreferencesWindow(self.props.active_window).present()

    def on_shortcuts(self, *_args):
        # PyGObject registers these newer Adwaita widget types lazily.  Touch
        # them before Gtk.Builder parses the resource so they are available to
        # the XML loader on all supported libadwaita versions.
        Adw.ShortcutsDialog
        Adw.ShortcutsSection
        Adw.ShortcutsItem
        builder = Gtk.Builder.new_from_resource("/io/github/Lluciocc/Groovia/shortcuts-dialog.ui")
        dialog = builder.get_object("shortcuts_dialog")
        dialog.present(self.props.active_window)

    def _window_action(self, method):
        window = self.props.active_window
        if window and hasattr(window, method):
            getattr(window, method)()

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
