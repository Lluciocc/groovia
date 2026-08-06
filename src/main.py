"""Groovia application entry point."""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio

from .mpris import MprisService
from .window import GrooviaWindow


class GrooviaApplication(Adw.Application):
    def __init__(self):
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
        self.create_action(
            "toggle-play", lambda *_: self._window_action("_toggle_play")
        )
        self.create_action(
            "next", lambda *_: self._window_action("_next"), ["<primary>Right"]
        )
        self.create_action(
            "previous", lambda *_: self._window_action("_previous"), ["<primary>Left"]
        )
        self.create_action(
            "mute", lambda *_: self._window_action("_toggle_mute"), ["m"]
        )

    def do_activate(self):
        win = self.props.active_window or GrooviaWindow(application=self)
        if not hasattr(self, "mpris"):
            self.mpris = MprisService(win)
        win.present()

    def do_open(self, files, _n_files, _hint):
        self.activate()
        win = self.props.active_window
        if win:
            win.open_paths([file.get_path() for file in files if file.get_path()])

    def on_about(self, *_args):
        Adw.AboutDialog(
            application_name="Groovia",
            application_icon="io.github.Lluciocc.Groovia",
            developer_name="Lluciocc",
            version="0.1.0",
            developers=["Lluciocc"],
            copyright="© 2026 Lluciocc",
        ).present(self.props.active_window)

    def on_preferences(self, *_args):
        from .preferences import PreferencesWindow

        PreferencesWindow(self.props.active_window).present()

    def on_shortcuts(self, *_args):
        dialog = Adw.ShortcutsDialog()
        dialog.present(self.props.active_window)

    def _window_action(self, method):
        window = self.props.active_window
        if window and hasattr(window, method):
            getattr(window, method)()

    def do_shutdown(self):
        if hasattr(self, "mpris"):
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


def main(_version):
    return GrooviaApplication().run(sys.argv)
