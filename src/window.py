import logging
import os
import random
import shutil
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import unquote, urlparse

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from .audio import AudioPlayer
from .autodj import AutoDJService
from .downloads import SpotDLService, classify_input
from .library import LibraryDatabase, LibraryScanner
from .library.scanner import FORMATS
from .models import Playlist, Track
from .platform_compat import IS_WINDOWS, iter_gtk_children, open_folder
from .visuals import css_rgb, mix, palette_for
from .widgets import LyricsView, VinylView

LOGGER = logging.getLogger("groovia.window")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[Groovia window] %(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


CSS = """
.groovia-window { color: @window_fg_color; background: @window_bg_color; }
.groovia-content, .groovia-content .navigation-page, .groovia-content .navigation-view,
.groovia-content .toolbar-view, .groovia-content .content-view { background: @window_bg_color; }
.sidebar { background: @headerbar_bg_color; }
.brand { padding: 18px 18px 14px; }
.brand-mark { color: #ff725e; font-size: 28px; }
.nav-row { min-height: 42px; padding: 0 10px; border-radius: 10px; margin: 2px 8px; }
.nav-row:selected { background: #ff725e; color: white; }
.nav-section { margin: 18px 16px 6px; color: alpha(@window_fg_color, .52); font-size: 11px; font-weight: 700; }
.hero { padding: 34px 42px 24px; }
.eyebrow { color: #ff9889; font-size: 12px; font-weight: 700; letter-spacing: 1px; }
.hero-title { font-size: 32px; font-weight: 800; }
.muted { color: alpha(@window_fg_color, .58); }
.section-title { font-size: 20px; font-weight: 750; margin-bottom: 10px; }
.album-card { background: @headerbar_bg_color; border-radius: 14px; padding: 10px; }
.album-card:hover { background: @card_bg_color; }
.album-art { border-radius: 10px; }
.album-title { font-weight: 700; margin-top: 8px; }
.album-meta { color: alpha(white, .55); font-size: 12px; }
.vinyl-panel { background: radial-gradient(circle at 50% 40%, #ff725e, #171721 53%, #111117 100%); border-radius: 22px; padding: 16px; }
.now-card { background: @headerbar_bg_color; border-radius: 18px; padding: 22px; }
.now-title { font-size: 25px; font-weight: 800; }
.track-row { padding: 9px 12px; border-radius: 10px; }
.track-row:hover { background: @card_bg_color; }
button.favorite-active { color: #f6d32d; }
.playlist-create-content { padding: 24px; }
.playlist-create-cover { border-radius: 14px; background: @card_bg_color; }
.playlist-create-hint { margin-top: 2px; }
.player-bar { background: @headerbar_bg_color; border-top: 1px solid @window_fg_color; padding: 8px 18px; }
.player-title { font-weight: 700; }
.progress { min-width: 220px; }
.queue-badge { background: #ff725e; color: white; border-radius: 99px; padding: 2px 7px; }
.auto-dj-badge { background: alpha(@accent_color, .18); color: @accent_color; border-radius: 99px; padding: 3px 8px; font-size: 11px; font-weight: 700; }
.empty-state { padding: 80px 24px; }
.lyrics-line { min-height: 42px; padding: 8px 18px; color: alpha(@window_fg_color, .58); font-size: 20px; }
.lyrics-line:hover { color: @window_fg_color; background: alpha(@window_fg_color, .08); }
.lyrics-line:focus-visible { outline: 2px solid @accent_color; outline-offset: 2px; }
.lyrics-current { color: @window_fg_color; font-size: 20px; font-weight: 800; }
.lyrics-word-line { padding: 8px 12px; }
.lyrics-word { padding: 2px 1px; margin: 0; color: alpha(@window_fg_color, .66); font-size: 20px; }
.lyrics-word:hover { color: @window_fg_color; background: alpha(@window_fg_color, .08); }
.lyrics-word:focus-visible { outline: 2px solid @accent_color; outline-offset: 2px; }
.lyrics-word-current { color: @window_fg_color; font-weight: 800; }
.lyrics-word-previous { color: alpha(@window_fg_color, .82); }
.lyrics-word-upcoming { color: alpha(@window_fg_color, .52); }
"""


def icon_button(icon: str, tooltip: str, callback=None) -> Gtk.Button:
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
    button.add_css_class("flat")
    if callback:
        button.connect("clicked", callback)
    return button


def cover_widget(path: str | None, size: int = 72) -> Gtk.Widget:
    if path and Path(path).exists():
        # Gtk.Image's pixel size is a hard visual bound; Gtk.Picture otherwise
        # keeps the cover's natural resolution in the player bar.
        picture = Gtk.Image.new_from_file(path)
        picture.set_pixel_size(size)
    else:
        picture = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
        picture.set_pixel_size(max(28, size // 2))
    picture.set_hexpand(False)
    picture.set_vexpand(False)
    picture.set_size_request(size, size)
    picture.add_css_class("album-art")
    return picture


class GrooviaWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.add_css_class("groovia-window")
        self.set_title("Groovia")
        self.set_default_size(1180, 780)
        self.set_size_request(720, 520)
        self.database = LibraryDatabase()
        self.scanner = LibraryScanner(self.database)
        self.download_service = SpotDLService(
            self.database, self.scanner, callback=self._download_event
        )
        self.player = AudioPlayer()
        self.auto_dj = AutoDJService(self._on_auto_dj_plan)
        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect(
            "notify::accent-color", self._on_system_style_changed
        )
        self.style_manager.connect("notify::dark", self._on_system_style_changed)
        self.queue: list[Track] = self.database.load_queue()
        self.repeat_mode = "all"
        self.repeat_all = True
        self.shuffle = False
        self._library_random_mode = False
        self._playback_source: list[Track] = []
        self._history: list[Track] = []
        self.current: Track | None = None
        self._current_playlist_id: int | None = None
        self._playlist_views: dict[int, dict] = {}
        self.playlist_assets_dir = self.database.path.parent / "playlists"
        self.playlist_assets_dir.mkdir(parents=True, exist_ok=True)
        self._settings = self._load_settings()
        self._apply_crossfade_setting()
        self._apply_auto_dj_setting()
        if self._settings:
            self._settings.connect(
                "changed::crossfade-index", self._on_crossfade_setting_changed
            )
            self._settings.connect(
                "changed::auto-dj-enabled", self._on_auto_dj_setting_changed
            )
            for key in (
                "auto-dj-style",
                "auto-dj-beat-matching",
                "auto-dj-phrase-matching",
                "auto-dj-tempo-matching",
                "auto-dj-smart-eq",
                "auto-dj-silence-detection",
                "auto-dj-length",
            ):
                self._settings.connect(
                    f"changed::{key}", self._on_auto_dj_setting_changed
                )
        self._palette_cache = {}
        self._palette = self._system_palette()
        self._palette_animation = 0
        self._apply_css()
        self._build_ui()
        self._install_global_key_controller()
        self._connect_player()
        self._refresh_library()
        self._refresh_playlist_sidebar()
        self._restore_playback()
        GLib.idle_add(self._automatic_playlist_sync)

    def _load_settings(self):
        try:
            return Gio.Settings.new("io.github.Lluciocc.Groovia")
        except Exception:
            return None

    def _apply_crossfade_setting(self):
        values = (0.0, 1.0, 3.0, 5.0, 8.0, 10.0)
        index = self._settings.get_int("crossfade-index") if self._settings else 3
        self.player.crossfade = values[max(0, min(index, len(values) - 1))]

    def _on_crossfade_setting_changed(self, *_args):
        self._apply_crossfade_setting()
        self._prepare_next_track()

    def _apply_auto_dj_setting(self):
        enabled = bool(self._settings and self._settings.get_boolean("auto-dj-enabled"))
        enabled = enabled and self.repeat_mode != "one"
        self._auto_dj_enabled = enabled
        self.player.set_auto_dj_enabled(enabled)
        if not enabled:
            self.auto_dj.cancel()
            self.player.set_auto_dj_plan(None)

    def _on_auto_dj_setting_changed(self, *_args):
        self._apply_auto_dj_setting()
        self._prepare_next_track()

    def _auto_dj_options(self):
        settings = self._settings
        if not settings:
            return {
                "style": "balanced",
                "length": "automatic",
                "beat_matching": True,
                "phrase_matching": True,
                "tempo_matching": True,
                "smart_eq": True,
                "silence_detection": True,
            }
        return {
            "style": settings.get_string("auto-dj-style"),
            "length": settings.get_string("auto-dj-length"),
            "beat_matching": settings.get_boolean("auto-dj-beat-matching"),
            "phrase_matching": settings.get_boolean("auto-dj-phrase-matching"),
            "tempo_matching": settings.get_boolean("auto-dj-tempo-matching"),
            "smart_eq": settings.get_boolean("auto-dj-smart-eq"),
            "silence_detection": settings.get_boolean("auto-dj-silence-detection"),
        }

    def _on_auto_dj_plan(self, plan):
        if not self._auto_dj_enabled or not self.current or not self.player.next_track:
            return
        if (
            plan.current_path != self.current.path
            or plan.next_path != self.player.next_track.path
        ):
            return
        self.player.set_auto_dj_plan(plan)

    def _system_palette(self):
        rgba = self.style_manager.get_accent_color_rgba()
        accent = (rgba.red, rgba.green, rgba.blue)
        factor = 0.16 if self.style_manager.get_dark() else 0.72
        background = tuple(max(0.035, value * factor) for value in accent)
        return accent, background

    def _on_system_style_changed(self, *_args):
        if (
            not self.current
            or not self.current.cover_path
            or not Path(self.current.cover_path).exists()
        ):
            self._set_album_palette(None)

    @staticmethod
    def _load_css(provider: Gtk.CssProvider, css: str) -> None:
        """Load CSS across PyGObject builds with different signatures."""
        try:
            provider.load_from_data(css)
        except TypeError:
            provider.load_from_data(css, len(css))

    def _apply_css(self):
        provider = Gtk.CssProvider()
        self._load_css(provider, CSS)
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._dynamic_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, self._dynamic_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
        )
        self._update_palette_css(*self._palette)

    def _update_palette_css(self, accent, background):
        accent_css = css_rgb(accent)
        background_css = css_rgb(background)
        css = f"""
        .groovia-content, .groovia-content .toolbar-view {{ background: linear-gradient(135deg, {background_css}, @window_bg_color 78%); }}
        .brand-mark, .eyebrow {{ color: {accent_css}; }}
        .nav-row:selected {{ background: {accent_css}; color: white; }}
        button.suggested-action, .suggested-action {{ background-color: {accent_css}; color: white; }}
        .headerbar, .player-bar {{ border-color: {accent_css}; }}
        .now-card {{ border: 1px solid {accent_css}; }}
        .vinyl-panel {{ background: radial-gradient(circle at 50% 40%, {accent_css}, {background_css} 54%, #111117 100%); }}
        """
        self._load_css(self._dynamic_provider, css)
        if hasattr(self, "vinyl"):
            self.vinyl.set_accent(accent)

    def _set_album_palette(self, cover_path):
        if cover_path:
            target = palette_for(cover_path, self._palette_cache)
        else:
            target = self._system_palette()
        if target is None or target == self._palette:
            return
        if self._palette_animation:
            GLib.source_remove(self._palette_animation)
        self._palette_start = self._palette
        self._palette_target = target
        self._palette_started_at = time.monotonic()
        self._palette_animation = GLib.timeout_add(16, self._animate_palette)

    def _animate_palette(self):
        progress = min(1.0, (time.monotonic() - self._palette_started_at) / 0.72)
        accent = mix(self._palette_start[0], self._palette_target[0], progress)
        background = mix(self._palette_start[1], self._palette_target[1], progress)
        self._palette = (accent, background)
        self._update_palette_css(accent, background)
        if progress >= 1:
            self._palette_animation = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _build_ui(self):
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)
        self.stack.add_named(self._home_page(), "home")
        self.stack.add_named(self._library_page(), "library")
        self.stack.add_named(self._queue_page(), "queue")
        self.stack.add_named(self._collection_page("album"), "album-detail")
        self.stack.add_named(self._collection_page("artist"), "artist-detail")
        self.stack.add_named(self._lyrics_page(), "lyrics")

        sidebar = self._sidebar()
        split = Adw.NavigationSplitView()
        split.set_sidebar(Adw.NavigationPage.new(sidebar, "Library"))
        split.set_content(Adw.NavigationPage.new(self.stack, "Groovia"))
        split.set_min_sidebar_width(190)
        split.set_max_sidebar_width(240)
        self.split = split

        toolbar = Adw.ToolbarView()
        toolbar.add_css_class("groovia-content")
        toolbar.add_top_bar(self._header())
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(split)
        toolbar.set_content(self.toast_overlay)
        toolbar.add_bottom_bar(self._player_bar())
        self.set_content(toolbar)
        self._install_drop_target(toolbar)

    def _install_drop_target(self, widget):
        try:
            target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
            target.connect("drop", self._on_files_dropped)
            target.connect("enter", lambda *_: widget.add_css_class("drop-target"))
            target.connect("leave", lambda *_: widget.remove_css_class("drop-target"))
            widget.add_controller(target)
        except (AttributeError, TypeError):
            # Older GTK builds can still open files through the desktop file.
            pass

    def _on_files_dropped(self, _target, value, _x, _y):
        paths = [file.get_path() for file in value.get_files() if file.get_path()]
        self.open_paths(paths)
        return bool(paths)

    def _header(self):
        header = Adw.HeaderBar()
        header.set_show_title(False)
        toggle = icon_button(
            "sidebar-show-symbolic",
            "Toggle navigation",
            lambda *_: self.split.set_show_sidebar(not self.split.get_show_sidebar()),
        )
        header.pack_start(toggle)
        brand = Gtk.Label(label="Groovia")
        brand.add_css_class("title-2")
        header.set_title_widget(brand)
        header.pack_end(
            icon_button(
                "view-list-symbolic", "Queue", lambda *_: self._show_page("queue")
            )
        )
        menu = Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Main Menu")
        menu.set_menu_model(self._menu_model())
        header.pack_end(menu)
        return header

    def _menu_model(self):
        menu = Gio.Menu()
        menu.append("Preferences", "app.preferences")
        menu.append("Keyboard Shortcuts", "app.shortcuts")
        menu.append("About Groovia", "app.about")
        return menu

    def _sidebar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("sidebar")
        brand = Gtk.Box(spacing=10)
        brand.add_css_class("brand")
        mark = Gtk.Label(label="◉")
        mark.add_css_class("brand-mark")
        brand.append(mark)
        name = Gtk.Label(label="Groovia", halign=Gtk.Align.START)
        name.add_css_class("title-2")
        brand.append(name)
        box.append(brand)
        self.nav_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.nav_list.add_css_class("navigation-sidebar")
        self.nav_list.connect("row-selected", self._on_nav_selected)
        for title, icon, page in (
            ("Home", "go-home-symbolic", "home"),
            ("All Music", "audio-x-generic-symbolic", "library"),
            ("Queue", "view-list-symbolic", "queue"),
        ):
            row = Gtk.ListBoxRow()
            row.set_name(page)
            content = Gtk.Box(spacing=12)
            content.add_css_class("nav-row")
            content.append(Gtk.Image.new_from_icon_name(icon))
            content.append(Gtk.Label(label=title, xalign=0))
            row.set_child(content)
            self.nav_list.append(row)
        label = Gtk.Label(label="YOUR COLLECTION", xalign=0)
        label.add_css_class("nav-section")
        box.append(label)
        box.append(self.nav_list)
        playlist_label = Gtk.Label(label="PLAYLISTS", xalign=0)
        playlist_label.add_css_class("nav-section")
        box.append(playlist_label)
        self.playlist_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.playlist_list.add_css_class("navigation-sidebar")
        self.playlist_list.connect("row-selected", self._on_playlist_selected)
        box.append(self.playlist_list)
        new_playlist = Gtk.Button(label="New Playlist", icon_name="list-add-symbolic")
        new_playlist.add_css_class("flat")
        new_playlist.set_margin_start(14)
        new_playlist.set_margin_end(14)
        new_playlist.set_margin_top(8)
        new_playlist.connect("clicked", lambda *_: self._create_playlist_dialog())
        box.append(new_playlist)
        spacer = Gtk.Box(vexpand=True)
        box.append(spacer)
        import_button = Gtk.Button(
            label="Import music folder", icon_name="folder-music-symbolic"
        )
        import_button.add_css_class("suggested-action")
        import_button.set_margin_start(14)
        import_button.set_margin_end(14)
        import_button.set_margin_bottom(14)
        import_button.connect("clicked", self._choose_folder)
        box.append(import_button)
        return box

    def _home_page(self):
        root = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.add_css_class("hero")
        intro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        intro.append(
            Gtk.Label(label="YOUR MUSIC, YOUR SPACE", xalign=0, css_classes=["eyebrow"])
        )
        intro.append(
            Gtk.Label(label="Good evening", xalign=0, css_classes=["hero-title"])
        )
        # intro.append(Gtk.Label(label="Put on a record and let the room change.", xalign=0, css_classes=["muted"]))
        actions = Gtk.Box(spacing=8, margin_top=18)
        imp = Gtk.Button(label="Import music", icon_name="folder-music-symbolic")
        imp.add_css_class("suggested-action")
        imp.connect("clicked", self._choose_folder)
        actions.append(imp)
        download = Gtk.Button(
            label="Download from URL", icon_name="document-save-symbolic"
        )
        download.connect("clicked", self._download_url)
        actions.append(download)
        intro.append(actions)
        content.append(intro)

        now = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        now.add_css_class("now-card")
        now.set_margin_top(28)
        self.vinyl = VinylView(halign=Gtk.Align.CENTER)
        self.vinyl.set_size_request(560, 560)
        self.vinyl.add_css_class("vinyl-panel")
        self.vinyl.connect("seek-requested", self._on_vinyl_seek)
        self.vinyl.connect("toggle-play", lambda *_: self._toggle_play())
        now.append(self.vinyl)
        details = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.CENTER
        )
        details.append(
            Gtk.Label(label="NOW PLAYING", xalign=0, css_classes=["eyebrow"])
        )
        self.now_title = Gtk.Label(
            label="Choose an album to start listening", xalign=0, wrap=True
        )
        self.now_title.add_css_class("now-title")
        self.now_title.set_xalign(0.5)
        details.append(self.now_title)
        self.now_artist = Gtk.Label(
            label="Your local library is ready when you are.",
            xalign=0,
            css_classes=["muted"],
        )
        self.now_artist.set_xalign(0.5)
        details.append(self.now_artist)
        self.now_album = Gtk.Label(label="", xalign=0, css_classes=["muted"])
        self.now_album.set_xalign(0.5)
        details.append(self.now_album)
        self.now_play = Gtk.Button(
            label="Play something",
            icon_name="media-playback-start-symbolic",
            halign=Gtk.Align.CENTER,
        )
        self.now_play.add_css_class("pill")
        self.now_play.connect("clicked", lambda *_: self._toggle_play())
        details.append(self.now_play)
        now.append(details)
        content.append(now)

        content.append(
            Gtk.Label(
                label="Recently added",
                xalign=0,
                css_classes=["section-title"],
                margin_top=28,
            )
        )
        self.album_flow = Gtk.FlowBox(
            max_children_per_line=6,
            min_children_per_line=2,
            selection_mode=Gtk.SelectionMode.NONE,
            row_spacing=12,
            column_spacing=12,
        )
        content.append(self.album_flow)
        content.append(
            Gtk.Label(
                label="Recently played",
                xalign=0,
                css_classes=["section-title"],
                margin_top=28,
            )
        )
        self.recent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        content.append(self.recent_box)
        empty = Gtk.Label(
            label="Import a folder to bring your records into Groovia.",
            css_classes=["muted", "empty-state"],
        )
        self.empty_home = empty
        content.append(empty)
        root.set_child(content)
        return root

    def _library_page(self):
        root = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.library_scroll = root
        root.get_vadjustment().connect("value-changed", self._on_library_scroll)
        self.library_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.library_box.set_margin_top(28)
        self.library_box.set_margin_start(38)
        self.library_box.set_margin_end(38)
        self.search_entry = Gtk.SearchEntry(
            placeholder_text="Search your library", hexpand=True
        )
        self.search_entry.set_margin_bottom(18)
        self.search_entry.connect(
            "search-changed", lambda entry: self._refresh_library(entry.get_text())
        )
        self.library_box.append(self.search_entry)
        self.library_content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        self.library_box.append(self.library_content_box)
        self.library_items_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        self._library_tracks = []
        self._library_cursor = 0
        self._library_batch_size = 40
        self._library_loading = False
        root.set_child(self.library_box)
        return root

    def _queue_page(self):
        root = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(28)
        box.set_margin_start(38)
        box.set_margin_end(38)
        head = Gtk.Box()
        head.append(
            Gtk.Label(label="Queue", xalign=0, css_classes=["hero-title"], hexpand=True)
        )
        clear = Gtk.Button(label="Clear", tooltip_text="Clear queue")
        clear.connect("clicked", lambda *_: self._clear_queue())
        head.append(clear)
        box.append(head)
        self.queue_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.append(self.queue_box)
        self.queue_empty = Gtk.Label(
            label="Your queue is empty.", css_classes=["muted", "empty-state"]
        )
        box.append(self.queue_empty)
        root.set_child(box)
        return root

    def _collection_page(self, kind: str):
        root = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(28)
        box.set_margin_start(38)
        box.set_margin_end(38)
        box.set_margin_bottom(28)
        back = Gtk.Button(
            label="Back to All Music",
            icon_name="go-previous-symbolic",
            halign=Gtk.Align.START,
        )
        back.connect("clicked", lambda *_: self._show_page("library"))
        box.append(back)
        heading = Gtk.Label(xalign=0, css_classes=["hero-title"])
        subtitle = Gtk.Label(xalign=0, css_classes=["muted"])
        items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(heading)
        box.append(subtitle)
        box.append(items)
        setattr(self, f"{kind}_detail_heading", heading)
        setattr(self, f"{kind}_detail_subtitle", subtitle)
        setattr(self, f"{kind}_detail_items", items)
        root.set_child(box)
        return root

    def _lyrics_page(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header = Gtk.Box(spacing=10)
        header.set_margin_top(18)
        header.set_margin_start(24)
        header.set_margin_end(24)
        back = Gtk.Button(label="Back", icon_name="go-previous-symbolic")
        back.connect("clicked", lambda *_: self._show_page("library"))
        header.append(back)
        title_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True
        )
        title = Gtk.Label(label="Lyrics", xalign=0, css_classes=["title-2"])
        subtitle = Gtk.Label(label="", xalign=0, css_classes=["muted"])
        title_box.append(title)
        title_box.append(subtitle)
        header.append(title_box)
        return_current = Gtk.Button(
            label="Return to current lyric", icon_name="find-location-symbolic"
        )
        return_current.set_visible(False)
        header.append(return_current)
        mode_switch = Gtk.Box(spacing=4)
        line_mode = Gtk.ToggleButton(label="Lines")
        word_mode = Gtk.ToggleButton(label="Words")
        word_mode.set_group(line_mode)
        line_mode.set_active(True)
        mode_switch.append(line_mode)
        mode_switch.append(word_mode)
        mode_switch.set_visible(False)
        header.append(mode_switch)
        fullscreen = Gtk.Button(
            icon_name="view-fullscreen-symbolic", tooltip_text="Fullscreen lyrics"
        )
        fullscreen.connect("clicked", lambda *_: self._open_lyrics_fullscreen())
        header.append(fullscreen)
        root.append(header)

        content = Gtk.Stack(
            vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE
        )
        empty = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        empty.append(Gtk.Image.new_from_icon_name("text-x-generic-symbolic"))
        empty.append(Gtk.Label(label="No lyrics available", css_classes=["title-2"]))
        empty.append(
            Gtk.Label(
                label="Import an LRC file or search for lyrics later.",
                css_classes=["muted"],
            )
        )
        empty_actions = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        find = Gtk.Button(label="Find Lyrics", icon_name="system-search-symbolic")
        import_button = Gtk.Button(
            label="Import LRC File", icon_name="document-open-symbolic"
        )
        manual_button = Gtk.Button(
            label="Add Lyrics Manually", icon_name="list-add-symbolic"
        )
        empty_actions.append(find)
        empty_actions.append(import_button)
        empty_actions.append(manual_button)
        empty.append(empty_actions)
        view = LyricsView()
        content.add_named(empty, "empty")
        content.add_named(view, "lyrics")
        overlay = Gtk.Overlay()
        background = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, opacity=0.12)
        background.set_can_shrink(True)
        overlay.set_child(background)
        overlay.add_overlay(content)
        root.append(overlay)
        footer = Gtk.Box(spacing=8)
        footer.set_margin_top(8)
        footer.set_margin_bottom(14)
        footer.set_margin_start(24)
        footer.set_margin_end(24)
        status = Gtk.Label(label="", xalign=0, hexpand=True, css_classes=["muted"])
        minus = Gtk.Button(label="− 0.5 s", tooltip_text="Advance lyrics")
        plus = Gtk.Button(label="+ 0.5 s", tooltip_text="Delay lyrics")
        footer.append(status)
        footer.append(minus)
        footer.append(plus)
        root.append(footer)
        find.connect("clicked", lambda *_: self._find_current_lyrics())
        import_button.connect("clicked", lambda *_: self._import_current_lyrics())
        manual_button.connect("clicked", lambda *_: self._add_manual_lyrics())
        view.connect("seek-requested", lambda _view, seconds: self.player.seek(seconds))
        view.connect("manual-scroll", lambda *_: return_current.set_visible(True))
        view.connect(
            "mode-changed", lambda current, _mode: self._lyrics_mode_changed(current)
        )
        line_mode.connect(
            "toggled",
            lambda button: view.set_mode("line") if button.get_active() else None,
        )
        word_mode.connect(
            "toggled",
            lambda button: view.set_mode("word") if button.get_active() else None,
        )
        return_current.connect(
            "clicked",
            lambda *_: (view.return_to_current(), return_current.set_visible(False)),
        )
        minus.connect("clicked", lambda *_: self._adjust_lyrics_offset(-500))
        plus.connect("clicked", lambda *_: self._adjust_lyrics_offset(500))
        self._lyrics_widgets = {
            "root": root,
            "content": content,
            "empty": empty,
            "view": view,
            "title": title,
            "subtitle": subtitle,
            "status": status,
            "return": return_current,
            "find": find,
            "import": import_button,
            "manual": manual_button,
            "background": background,
            "mode_switch": mode_switch,
            "line_mode": line_mode,
            "word_mode": word_mode,
        }
        return root

    def _show_lyrics(self, track=None):
        track = track or self.current
        if not track:
            self._toast("Nothing is playing")
            return
        self._resolve_cover(track)
        variants = self.download_service.lyrics.find_variants(track)
        widgets = self._lyrics_widgets
        same_track = getattr(self, "_lyrics_track", None) is track
        preferred_mode = widgets["view"].mode if same_track else "line"
        if track.cover_path and Path(track.cover_path).is_file():
            widgets["background"].set_filename(track.cover_path)
        else:
            widgets["background"].set_filename(None)
        widgets["title"].set_label(track.title)
        widgets["subtitle"].set_label(f"{track.artist} · {track.album}")
        widgets["view"].set_documents(variants, preferred_mode=preferred_mode)
        timeline = widgets["view"].document
        row = widgets["view"].selected_row
        widgets["content"].set_visible_child_name("lyrics" if timeline else "empty")
        available_modes = widgets["view"].available_modes
        widgets["mode_switch"].set_visible(len(available_modes) > 1)
        widgets["line_mode"].set_active(widgets["view"].mode == "line")
        widgets["word_mode"].set_active(widgets["view"].mode == "word")
        if timeline:
            widgets["status"].set_label(
                "Synchronized lyrics"
                if timeline.synchronized
                else "Unsynchronized lyrics"
            )
            widgets["find"].set_visible(False)
        else:
            widgets["status"].set_label("No lyrics found")
            widgets["find"].set_visible(True)
        self._lyrics_track = track
        self._lyrics_row = row
        self._show_page("lyrics")
        if timeline and self.current is track:
            widgets["view"].update_position(int(self.player.position * 1000))
        if getattr(self, "_lyrics_fullscreen_view", None):
            self._lyrics_fullscreen_view.set_documents(
                variants, preferred_mode=widgets["view"].mode or "line"
            )
            self._set_fullscreen_lyrics_cover(track)

    def _lyrics_mode_changed(self, view):
        if (
            not hasattr(self, "_lyrics_widgets")
            or view is not self._lyrics_widgets["view"]
        ):
            return
        self._lyrics_row = view.selected_row
        mode_label = {
            "line": "Line-by-line",
            "word": "Word-by-word",
            "plain": "Plain",
        }.get(view.mode)
        timeline = view.document
        if timeline and mode_label:
            self._lyrics_widgets["status"].set_label(f"{mode_label} lyrics")
        fullscreen_view = getattr(self, "_lyrics_fullscreen_view", None)
        if fullscreen_view is not None and view.mode in fullscreen_view.available_modes:
            fullscreen_view.set_mode(view.mode)

    def _update_lyrics_for_current(self):
        if not hasattr(self, "_lyrics_widgets"):
            return
        if self.stack.get_visible_child_name() == "lyrics":
            self._show_lyrics(self.current)

    def _find_current_lyrics(self):
        if not self.current:
            return
        settings = self._settings
        providers = tuple(
            item.strip()
            for item in (
                settings.get_string("lyrics-providers")
                if settings
                else "synced,genius,musixmatch,azlyrics"
            ).split(",")
            if item.strip()
        )
        self.download_service.find_lyrics(
            self.current, providers=providers, fallback=True
        )
        self._toast("Searching for lyrics…")

    def _add_manual_lyrics(self):
        if not self.current:
            return
        dialog = Gtk.Dialog(title="Add Lyrics", transient_for=self, modal=True)
        dialog.set_default_size(620, 500)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        save = dialog.add_button("Save", Gtk.ResponseType.OK)
        save.add_css_class("suggested-action")
        editor = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True, min_content_height=350)
        scroll.set_margin_top(18)
        scroll.set_margin_bottom(18)
        scroll.set_margin_start(18)
        scroll.set_margin_end(18)
        scroll.set_child(editor)
        dialog.get_content_area().append(scroll)

        def response(current, response_id):
            if response_id == Gtk.ResponseType.OK:
                buffer = editor.get_buffer()
                text = buffer.get_text(
                    buffer.get_start_iter(), buffer.get_end_iter(), False
                )
                if text.strip() and self.download_service.lyrics.save_text(
                    self.current, text, synchronized=False
                ):
                    self._show_lyrics(self.current)
                    self._toast("Lyrics saved")
            current.close()

        dialog.connect("response", response)
        dialog.present()

    def _import_current_lyrics(self):
        if not self.current:
            return
        chooser = Gtk.FileDialog(title="Import lyrics")
        chooser.open(
            self,
            None,
            lambda dialog, result: self._lyrics_file_selected(
                dialog, result, self.current
            ),
        )

    def _lyrics_file_selected(self, dialog, result, track):
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return
        path = file.get_path()
        if Path(path).suffix.lower() not in {".lrc", ".txt"}:
            self._toast("Choose an .lrc or .txt file")
            return
        if self.download_service.lyrics.import_file(track, path):
            self._show_lyrics(track)
            self._toast("Lyrics imported")
        else:
            self._toast("Could not import lyrics")

    def _adjust_lyrics_offset(self, amount):
        if not getattr(self, "_lyrics_row", None) or self._lyrics_row.get("id") is None:
            return
        view = self._lyrics_widgets["view"]
        for row in view.variant_rows:
            offset = int(row.get("timing_offset_ms") or 0) + amount
            self.database.update_lyrics_offset(row["id"], offset)
        self._show_lyrics(self._lyrics_track)

    def _open_lyrics_fullscreen(self):
        if not self.current:
            return
        if getattr(self, "_lyrics_fullscreen_window", None):
            self._lyrics_fullscreen_window.present()
            return
        self._resolve_cover(self.current)
        variants = self.download_service.lyrics.find_variants(self.current)
        window = Gtk.Window(title=f"Lyrics — {self.current.title}", transient_for=self)
        window.set_default_size(1000, 720)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        top = Gtk.Box(spacing=10, margin_top=16, margin_start=20, margin_end=20)
        top.append(
            Gtk.Label(
                label=self.current.title,
                xalign=0,
                css_classes=["title-2"],
                hexpand=True,
            )
        )
        close = Gtk.Button(
            icon_name="view-restore-symbolic", tooltip_text="Exit fullscreen"
        )
        close.connect("clicked", lambda *_: window.close())
        top.append(close)

        background = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, opacity=0.22)
        background.set_can_shrink(True)
        cover_path = (
            self.current.cover_path
            if self.current.cover_path and Path(self.current.cover_path).is_file()
            else None
        )
        if cover_path:
            background.set_filename(cover_path)
        view = LyricsView()
        main_view = (
            self._lyrics_widgets["view"] if hasattr(self, "_lyrics_widgets") else None
        )
        view.set_documents(
            variants, preferred_mode=main_view.mode if main_view else "line"
        )
        timeline = view.document
        view.connect("seek-requested", lambda _view, seconds: self.player.seek(seconds))
        if len(view.available_modes) > 1:
            line_mode = Gtk.ToggleButton(label="Lines")
            word_mode = Gtk.ToggleButton(label="Words")
            word_mode.set_group(line_mode)
            line_mode.set_active(view.mode == "line")
            word_mode.set_active(view.mode == "word")
            line_mode.connect(
                "toggled",
                lambda button: view.set_mode("line") if button.get_active() else None,
            )
            word_mode.connect(
                "toggled",
                lambda button: view.set_mode("word") if button.get_active() else None,
            )
            top.append(line_mode)
            top.append(word_mode)
        lyrics_overlay = Gtk.Overlay()
        lyrics_overlay.set_child(background)
        lyrics_overlay.add_overlay(view)
        root.append(lyrics_overlay)
        window.set_child(root)
        self._lyrics_fullscreen_window = window
        self._lyrics_fullscreen_view = view
        self._lyrics_fullscreen_background = background
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._fullscreen_lyrics_key_pressed, window)
        window.add_controller(keys)
        window.connect(
            "close-request", lambda current: self._close_lyrics_fullscreen(current)
        )
        window.present()
        try:
            window.fullscreen()
        except AttributeError:
            pass

    def _close_lyrics_fullscreen(self, window):
        self._lyrics_fullscreen_window = None
        self._lyrics_fullscreen_view = None
        self._lyrics_fullscreen_background = None
        return False

    @staticmethod
    def _fullscreen_lyrics_key_pressed(
        _controller, keyval, _keycode, _state, window
    ):
        if keyval == Gdk.KEY_Escape:
            window.close()
            return True
        return False

    def _set_fullscreen_lyrics_cover(self, track):
        background = getattr(self, "_lyrics_fullscreen_background", None)
        if background is None:
            return
        cover_path = (
            track.cover_path
            if track and track.cover_path and Path(track.cover_path).is_file()
            else None
        )
        background.set_filename(cover_path)

    def _playlist_page(self, playlist_id: int):
        root = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(28)
        content.set_margin_start(38)
        content.set_margin_end(38)
        content.set_margin_bottom(28)

        hero = Gtk.Box(spacing=18)
        cover_slot = Gtk.Box(width_request=180, height_request=180)
        hero.append(cover_slot)
        details = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            valign=Gtk.Align.CENTER,
            hexpand=True,
        )
        heading = Gtk.Label(xalign=0, css_classes=["hero-title"])
        subtitle = Gtk.Label(xalign=0, css_classes=["muted"])
        details.append(heading)
        details.append(subtitle)
        controls = Gtk.Box(spacing=8)
        play = Gtk.Button(label="Play", icon_name="media-playback-start-symbolic")
        play.add_css_class("suggested-action")
        play.connect("clicked", lambda *_: self._play_playlist(playlist_id))
        shuffle = Gtk.Button(
            label="Shuffle", icon_name="media-playlist-shuffle-symbolic"
        )
        shuffle.connect("clicked", lambda *_: self._play_playlist(playlist_id, True))
        more = Gtk.Button(label="More", icon_name="view-more-symbolic")
        more.connect(
            "clicked", lambda button: self._show_playlist_menu(button, playlist_id)
        )
        sync_button = Gtk.Button(label="Sync Now", icon_name="view-refresh-symbolic")
        sync_button.connect(
            "clicked", lambda *_: self._synchronize_playlist(playlist_id)
        )
        controls.append(play)
        controls.append(shuffle)
        controls.append(more)
        controls.append(sync_button)
        details.append(controls)
        hero.append(details)
        content.append(hero)

        tools = Gtk.Box(spacing=8)
        search = Gtk.SearchEntry(placeholder_text="Search this playlist", hexpand=True)
        sort = Gtk.DropDown.new_from_strings(
            ["Custom order", "Title", "Artist", "Album", "Duration", "Date added"]
        )
        sort.set_tooltip_text("Sort playlist tracks")
        tools.append(search)
        tools.append(sort)
        content.append(tools)

        tracks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        empty = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.CENTER
        )
        empty.append(
            Gtk.Label(label="This playlist is empty.", css_classes=["section-title"])
        )
        add = Gtk.Button(label="Add music", icon_name="list-add-symbolic")
        add.connect("clicked", lambda *_: self._show_page("library"))
        empty.append(add)
        content.append(tracks_box)
        content.append(empty)
        root.set_child(content)
        self._playlist_views[playlist_id] = {
            "root": root,
            "cover_slot": cover_slot,
            "heading": heading,
            "subtitle": subtitle,
            "search": search,
            "sort": sort,
            "tracks": tracks_box,
            "empty": empty,
            "sync_button": sync_button,
        }
        search.connect(
            "search-changed",
            lambda entry, pid=playlist_id: self._refresh_playlist_page(
                pid, entry.get_text()
            ),
        )
        sort.connect(
            "notify::selected",
            lambda _dropdown, pid=playlist_id: self._refresh_playlist_page(pid),
        )
        return root

    def _refresh_playlist_sidebar(self):
        for child in iter_gtk_children(self.playlist_list):
            self.playlist_list.remove(child)
        for playlist in self.database.playlists():
            row = Gtk.ListBoxRow()
            row.set_name(f"playlist:{playlist.id}")
            content = Gtk.Box(spacing=10)
            content.add_css_class("nav-row")
            icon = "starred-symbolic" if playlist.is_favorites else "view-list-symbolic"
            content.append(Gtk.Image.new_from_icon_name(icon))
            content.append(
                Gtk.Label(label=playlist.name, xalign=0, ellipsize=3, hexpand=True)
            )
            row.set_child(content)
            context = Gtk.GestureClick()
            context.set_button(Gdk.BUTTON_SECONDARY)
            context.connect(
                "pressed",
                lambda _gesture, _presses, x, y, pid=playlist.id, anchor=content: self._show_playlist_menu(
                    anchor, pid, x, y
                ),
            )
            content.add_controller(context)
            self.playlist_list.append(row)

    def _on_playlist_selected(self, _list, row):
        if row:
            try:
                self._show_playlist_page(int(row.get_name().split(":", 1)[1]))
            except (IndexError, ValueError):
                pass

    def _playlist_cover_path(self, playlist: Playlist) -> str:
        if playlist.cover_path and Path(playlist.cover_path).exists():
            return playlist.cover_path
        destination = self.playlist_assets_dir / f"playlist-{playlist.id}-default.svg"
        if not destination.exists():
            initial = escape((playlist.name.strip() or "P")[0].upper())
            title = escape(playlist.name[:24])
            destination.write_text(
                f"""<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#ff725e"/><stop offset="1" stop-color="#7e3f8f"/></linearGradient></defs>
<rect width="512" height="512" rx="36" fill="url(#g)"/><text x="256" y="275" text-anchor="middle" font-family="sans-serif" font-size="190" font-weight="700" fill="white">{initial}</text>
<text x="256" y="460" text-anchor="middle" font-family="sans-serif" font-size="26" fill="white" opacity=".85">{title}</text></svg>""",
                encoding="utf-8",
            )
        return str(destination)

    def _show_playlist_page(self, playlist_id: int):
        playlist = self.database.playlist(playlist_id)
        if not playlist:
            return
        page_name = f"playlist:{playlist_id}"
        if self.stack.get_child_by_name(page_name) is None:
            self.stack.add_named(self._playlist_page(playlist_id), page_name)
        self._current_playlist_id = playlist_id
        self._refresh_playlist_page(playlist_id)
        self._show_page(page_name)

    def _refresh_playlist_page(self, playlist_id: int, search: str | None = None):
        playlist = self.database.playlist(playlist_id)
        view = self._playlist_views.get(playlist_id)
        if not playlist or not view:
            return
        if search is None:
            search = view["search"].get_text()
        sort_names = ["custom", "title", "artist", "album", "duration", "date"]
        sort = sort_names[min(view["sort"].get_selected(), len(sort_names) - 1)]
        tracks = self.database.playlist_tracks(playlist_id, search, sort)
        for child in iter_gtk_children(view["cover_slot"]):
            view["cover_slot"].remove(child)
        view["cover_slot"].append(
            cover_widget(self._playlist_cover_path(playlist), 180)
        )
        view["heading"].set_label(playlist.name)
        total = sum(track.duration for track in tracks)
        status = ""
        if playlist.source_url:
            status = f" · {playlist.sync_status.replace('_', ' ').title()}"
            if playlist.last_sync_at:
                status += f" · Last sync {playlist.last_sync_at[:16].replace('T', ' ')}"
        subtitle_text = f"{len(tracks)} tracks · {self._time_label(total)}{status}"
        if self._settings and self._settings.get_boolean("lyrics-show-availability"):
            coverage = self.database.lyrics_coverage(
                [track.id for track in tracks if track.id is not None]
            )
            subtitle_text += f" · Lyrics {coverage.get('synced', 0)} synced / {coverage.get('plain', 0)} plain"
        view["subtitle"].set_label(subtitle_text)
        view["sync_button"].set_visible(bool(playlist.source_url or playlist.sync_file))
        for child in iter_gtk_children(view["tracks"]):
            view["tracks"].remove(child)
        for position, track in enumerate(tracks, 1):
            view["tracks"].append(self._track_row(track, True, playlist, position))
        view["empty"].set_visible(not tracks)

    def _refresh_playlist_pages(self):
        for playlist_id in list(self._playlist_views):
            self._refresh_playlist_page(playlist_id)

    def _show_playlist_menu(self, anchor, playlist_id, x=0, y=0):
        playlist = self.database.playlist(playlist_id)
        if not playlist:
            return
        if getattr(self, "_playlist_menu", None):
            self._playlist_menu.popdown()
        point = Gdk.Rectangle()
        point.x = round(x)
        point.y = round(y)
        point.width = 1
        point.height = 1
        popover = Gtk.Popover()
        popover.set_parent(anchor)
        popover.set_pointing_to(point)
        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu_box.set_margin_top(6)
        menu_box.set_margin_bottom(6)
        menu_box.set_margin_start(6)
        menu_box.set_margin_end(6)
        popover.set_child(menu_box)

        def add_button(label, callback, icon=None, sensitive=True):
            button = Gtk.Button(label=label, halign=Gtk.Align.FILL)
            if icon:
                button.set_icon_name(icon)
            button.add_css_class("flat")
            button.set_sensitive(sensitive)
            button.connect("clicked", lambda *_: (popover.popdown(), callback()))
            menu_box.append(button)

        add_button(
            "Play",
            lambda: self._play_playlist(playlist.id),
            "media-playback-start-symbolic",
        )
        add_button(
            "Shuffle",
            lambda: self._play_playlist(playlist.id, True),
            "media-playlist-shuffle-symbolic",
        )
        add_button("Play Next", lambda: self._play_playlist_next(playlist.id))
        add_button("Add to Queue", lambda: self._add_playlist_to_queue(playlist.id))
        menu_box.append(Gtk.Separator())
        add_button(
            "Rename",
            lambda: self._rename_playlist_dialog(playlist.id),
            sensitive=not playlist.is_favorites,
        )
        add_button("Change Cover", lambda: self._choose_playlist_cover(playlist.id))
        add_button("Duplicate", lambda: self._duplicate_playlist(playlist.id))
        if playlist.source_url or playlist.sync_file:
            menu_box.append(Gtk.Separator())
            add_button(
                "Synchronize Now",
                lambda: self._synchronize_playlist(playlist.id),
                "view-refresh-symbolic",
            )
            add_button(
                (
                    "Disable Automatic Synchronization"
                    if playlist.auto_sync != "manual"
                    else "Enable Automatic Synchronization"
                ),
                lambda: self._toggle_playlist_auto_sync(playlist.id),
            )
            add_button(
                "View Source Playlist", lambda: self._open_playlist_source(playlist.id)
            )
            add_button(
                "View Last Synchronization",
                lambda: self._show_sync_details(playlist.id),
            )
            add_button(
                "Repair Synchronization",
                lambda: self._repair_playlist_sync(playlist.id),
            )
            add_button(
                "Disconnect from Spotify Source",
                lambda: self._disconnect_playlist(playlist.id),
            )
        menu_box.append(Gtk.Separator())
        add_button(
            "Download Missing Lyrics",
            lambda: self._find_missing_playlist_lyrics(playlist.id),
        )
        add_button(
            "Refresh Lyrics",
            lambda: self._find_missing_playlist_lyrics(playlist.id, refresh=True),
        )
        add_button(
            "Show Lyrics Coverage",
            lambda: self._show_playlist_lyrics_coverage(playlist.id),
        )
        if not playlist.is_favorites:
            menu_box.append(Gtk.Separator())
            add_button(
                "Delete Playlist", lambda: self._confirm_delete_playlist(playlist.id)
            )
        popover.connect("closed", self._close_playlist_menu)
        self._playlist_menu = popover
        popover.popup()

    def _synchronize_playlist(self, playlist_id):
        settings = self._settings
        providers = tuple(
            item.strip()
            for item in (
                settings.get_string("lyrics-providers")
                if settings
                else "synced,genius,musixmatch,azlyrics"
            ).split(",")
            if item.strip()
        )
        job = self.download_service.synchronize(
            playlist_id,
            settings.get_string("sync-mode") if settings else None,
            settings.get_string("download-format") if settings else "mp3",
            settings.get_string("download-bitrate") if settings else "auto",
            lyrics_mode=(
                "synced"
                if settings and settings.get_boolean("lyrics-synced")
                else (
                    "plain"
                    if settings and settings.get_boolean("lyrics-fallback")
                    else "none"
                )
            ),
            lyrics_fallback=(
                settings.get_boolean("lyrics-fallback") if settings else True
            ),
            generate_lrc=(
                settings.get_boolean("lyrics-generate-lrc") if settings else True
            ),
            lyrics_providers=providers,
            sync_remove_lrc=(
                settings.get_boolean("lyrics-remove-sync") if settings else False
            ),
        )
        if job:
            self._toast("Playlist synchronization started")

    def _find_missing_playlist_lyrics(self, playlist_id, refresh=False):
        tracks = self.database.playlist_tracks(playlist_id)
        settings = self._settings
        providers = tuple(
            item.strip()
            for item in (
                settings.get_string("lyrics-providers")
                if settings
                else "synced,genius,musixmatch,azlyrics"
            ).split(",")
            if item.strip()
        )
        count = 0
        for track in tracks:
            if refresh or not self.download_service.lyrics.find(track)[0]:
                if self.download_service.find_lyrics(
                    track, providers=providers, fallback=True
                ):
                    count += 1
        self._toast(
            f"Searching lyrics for {count} track(s)…"
            if count
            else "No tracks need lyrics"
        )

    def _show_playlist_lyrics_coverage(self, playlist_id):
        tracks = self.database.playlist_tracks(playlist_id)
        coverage = self.database.lyrics_coverage(
            [track.id for track in tracks if track.id is not None]
        )
        synced = coverage.get("synced", 0)
        plain = coverage.get("plain", 0)
        total = len(tracks)
        dialog = Adw.AlertDialog(
            heading="Lyrics coverage",
            body=f"{synced} of {total} tracks have synchronized lyrics\n{plain} have plain lyrics\n{max(0, total - synced - plain)} have no lyrics",
        )
        dialog.add_response("close", "Close")
        dialog.present(self)

    def _toggle_playlist_auto_sync(self, playlist_id):
        playlist = self.database.playlist(playlist_id)
        if not playlist:
            return
        policy = "manual" if playlist.auto_sync != "manual" else "daily"
        self.database.update_playlist_source(playlist_id, auto_sync=policy)
        self._refresh_playlist_sidebar()
        self._refresh_playlist_page(playlist_id)
        self._toast(
            "Automatic synchronization enabled"
            if policy != "manual"
            else "Automatic synchronization disabled"
        )

    def _open_playlist_source(self, playlist_id):
        playlist = self.database.playlist(playlist_id)
        if playlist and playlist.source_url:
            try:
                Gio.AppInfo.launch_default_for_uri(playlist.source_url, None)
            except GLib.Error as error:
                self._toast(f"Could not open Spotify: {error.message}")

    def _show_sync_details(self, playlist_id):
        playlist = self.database.playlist(playlist_id)
        if not playlist:
            return
        dialog = Adw.AlertDialog(
            heading="Synchronization details",
            body=(
                f"Status: {playlist.sync_status.replace('_', ' ').title()}\n"
                f"Last result: {playlist.last_sync_result or 'Never synchronized'}\n"
                f"Sync file: {playlist.sync_file or 'Missing'}"
            ),
        )
        dialog.add_response("close", "Close")
        dialog.present(self)

    def _repair_playlist_sync(self, playlist_id):
        playlist = self.database.playlist(playlist_id)
        if not playlist:
            return
        if playlist.source_url:
            self._start_download(playlist.source_url, True, "sync")
        else:
            self._toast("The original Spotify URL is missing")

    def _disconnect_playlist(self, playlist_id):
        self.download_service.disconnect(playlist_id)
        self._refresh_playlist_sidebar()
        self._refresh_playlist_page(playlist_id)
        self._toast("Spotify synchronization disconnected")

    def _automatic_playlist_sync(self):
        monitor = Gio.NetworkMonitor.get_default()
        if monitor and not monitor.get_network_available():
            return GLib.SOURCE_REMOVE
        now = datetime.now(timezone.utc)
        for playlist in self.database.playlists():
            if not playlist.source_url or playlist.auto_sync == "manual":
                continue
            due = playlist.last_sync_at is None or playlist.auto_sync == "startup"
            if not due and playlist.last_sync_at:
                try:
                    last = datetime.fromisoformat(
                        playlist.last_sync_at.replace("Z", "+00:00")
                    )
                    interval = 7 if playlist.auto_sync == "weekly" else 1
                    due = (now - last).total_seconds() >= interval * 86400
                except ValueError:
                    due = True
            if due and self.download_service.manager.active is None:
                self._synchronize_playlist(playlist.id)
                break
        return GLib.SOURCE_REMOVE

    def _close_playlist_menu(self, popover):
        if popover.get_parent() is not None:
            popover.unparent()
        if getattr(self, "_playlist_menu", None) is popover:
            self._playlist_menu = None

    def _play_playlist(self, playlist_id, shuffle=False):
        tracks = self.database.playlist_tracks(playlist_id)
        if not tracks:
            self._toast("This playlist is empty")
            return
        if shuffle:
            random.shuffle(tracks)
        self._current_playlist_id = playlist_id
        self._library_random_mode = False
        self._history.clear()
        self._playback_source = tracks
        self.queue = tracks[1:]
        self.shuffle = False
        self._play_track(tracks[0])
        self._refresh_queue()

    def _play_playlist_next(self, playlist_id):
        tracks = self.database.playlist_tracks(playlist_id)
        if not tracks:
            self._toast("This playlist is empty")
            return
        self.queue[0:0] = tracks
        self._prepare_next_track()
        self._refresh_queue()
        self._toast(f"Added {len(tracks)} tracks to play next")

    def _add_playlist_to_queue(self, playlist_id):
        tracks = self.database.playlist_tracks(playlist_id)
        self.queue.extend(tracks)
        self._prepare_next_track()
        self._refresh_queue()
        self._toast(f"Added {len(tracks)} tracks to the queue")

    def _copy_playlist_cover(self, path, playlist_id):
        if not path or not Path(path).exists():
            return None
        suffix = Path(path).suffix.lower() or ".jpg"
        destination = self.playlist_assets_dir / f"playlist-{playlist_id}{suffix}"
        shutil.copy2(path, destination)
        return str(destination)

    def _create_playlist_dialog(self, track_to_add=None):
        dialog = Gtk.Dialog(title="New Playlist", transient_for=self, modal=True)
        dialog.set_default_size(500, 360)
        dialog.set_resizable(False)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Create", Gtk.ResponseType.OK)
        create_button = dialog.get_widget_for_response(Gtk.ResponseType.OK)
        if create_button:
            create_button.add_css_class("suggested-action")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.add_css_class("playlist-create-content")

        intro = Gtk.Box(spacing=14)
        intro_icon = Gtk.Image.new_from_icon_name("view-list-symbolic")
        intro_icon.set_pixel_size(34)
        intro_icon.add_css_class("accent")
        intro.append(intro_icon)
        intro_text = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True
        )
        intro_text.append(
            Gtk.Label(label="Create a playlist", xalign=0, css_classes=["title-2"])
        )
        intro_text.append(
            Gtk.Label(
                label="Collect your favorite tracks in one place.",
                xalign=0,
                wrap=True,
                css_classes=["dim-label", "playlist-create-hint"],
            )
        )
        intro.append(intro_text)
        content.append(intro)

        entry = Gtk.Entry(placeholder_text="Playlist name", hexpand=True)
        entry.set_activates_default(True)
        entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.PRIMARY, "view-list-symbolic"
        )
        content.append(entry)

        cover_card = Gtk.Box(spacing=14)
        cover_card.add_css_class("card")
        cover_card.set_margin_top(2)
        preview = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
        preview.set_pixel_size(72)
        preview.set_size_request(96, 96)
        preview.add_css_class("playlist-create-cover")
        cover_card.append(preview)
        cover_details = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=5,
            valign=Gtk.Align.CENTER,
            hexpand=True,
        )
        cover_details.append(
            Gtk.Label(label="Playlist cover", xalign=0, css_classes=["heading"])
        )
        cover_label = Gtk.Label(
            label="Use the generated cover",
            xalign=0,
            ellipsize=3,
            css_classes=["dim-label"],
        )
        cover_details.append(cover_label)
        choose = Gtk.Button(
            label="Choose an image",
            icon_name="image-x-generic-symbolic",
            halign=Gtk.Align.START,
        )
        choose.add_css_class("flat")
        choose.connect(
            "clicked",
            lambda *_: self._choose_cover_for_dialog(dialog, cover_label, preview),
        )
        cover_details.append(choose)
        cover_card.append(cover_details)
        content.append(cover_card)

        dialog.get_content_area().append(content)
        self._pending_playlist_cover = None
        dialog.connect("response", self._create_playlist_response, entry, track_to_add)
        dialog.present()

    def _choose_cover_for_dialog(self, dialog, label, preview=None):
        chooser = Gtk.FileDialog(title="Choose playlist cover")
        chooser.open(
            dialog,
            None,
            lambda current, result: self._playlist_dialog_cover_selected(
                current, result, label, preview
            ),
        )

    def _playlist_dialog_cover_selected(self, dialog, result, label, preview=None):
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return
        self._pending_playlist_cover = file.get_path()
        label.set_label(Path(self._pending_playlist_cover).name)
        if preview:
            preview.set_from_file(self._pending_playlist_cover)
            preview.set_pixel_size(72)

    def _create_playlist_response(self, dialog, response, entry, track_to_add):
        if response != Gtk.ResponseType.OK:
            dialog.close()
            return
        name = entry.get_text().strip()
        if not name:
            self._toast("Enter a playlist name")
            return
        try:
            playlist = self.database.create_playlist(name)
        except Exception:
            self._toast("A playlist with that name already exists")
            return
        if self._pending_playlist_cover:
            cover = self._copy_playlist_cover(self._pending_playlist_cover, playlist.id)
            self.database.update_playlist_cover(playlist.id, cover)
        if track_to_add:
            self.database.add_tracks_to_playlist(playlist.id, [track_to_add])
        self._pending_playlist_cover = None
        dialog.close()
        self._refresh_playlist_sidebar()
        self._refresh_playlist_pages()
        self._show_playlist_page(playlist.id)
        self._toast(f"Created {playlist.name}")

    def _rename_playlist_dialog(self, playlist_id):
        playlist = self.database.playlist(playlist_id)
        if not playlist or playlist.is_favorites:
            return
        dialog = Gtk.Dialog(title="Rename Playlist", transient_for=self, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Rename", Gtk.ResponseType.OK)
        entry = Gtk.Entry(text=playlist.name)
        entry.set_margin_top(18)
        entry.set_margin_bottom(18)
        entry.set_margin_start(18)
        entry.set_margin_end(18)
        dialog.get_content_area().append(entry)
        dialog.connect("response", self._rename_playlist_response, playlist_id, entry)
        dialog.present()

    def _rename_playlist_response(self, dialog, response, playlist_id, entry):
        if response == Gtk.ResponseType.OK and entry.get_text().strip():
            try:
                self.database.rename_playlist(playlist_id, entry.get_text().strip())
                self._refresh_playlist_sidebar()
                self._refresh_playlist_page(playlist_id)
            except Exception:
                self._toast("A playlist with that name already exists")
        dialog.close()

    def _choose_playlist_cover(self, playlist_id):
        chooser = Gtk.FileDialog(title="Choose playlist cover")
        chooser.open(
            self,
            None,
            lambda current, result: self._playlist_cover_selected(
                current, result, playlist_id
            ),
        )

    def _playlist_cover_selected(self, dialog, result, playlist_id):
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return
        cover = self._copy_playlist_cover(file.get_path(), playlist_id)
        if cover:
            self.database.update_playlist_cover(playlist_id, cover)
            self._refresh_playlist_sidebar()
            self._refresh_playlist_page(playlist_id)

    def _duplicate_playlist(self, playlist_id):
        playlist = self.database.playlist(playlist_id)
        if not playlist:
            return
        name = f"{playlist.name} Copy"
        suffix = 2
        existing = {item.name for item in self.database.playlists()}
        while name in existing:
            name = f"{playlist.name} Copy {suffix}"
            suffix += 1
        duplicate = self.database.create_playlist(name)
        if playlist.cover_path and Path(playlist.cover_path).exists():
            cover = self._copy_playlist_cover(playlist.cover_path, duplicate.id)
            self.database.update_playlist_cover(duplicate.id, cover)
        self.database.add_tracks_to_playlist(
            duplicate.id, self.database.playlist_tracks(playlist.id)
        )
        self._refresh_playlist_sidebar()
        self._refresh_playlist_pages()
        self._toast(f"Duplicated {playlist.name}")

    def _confirm_delete_playlist(self, playlist_id):
        playlist = self.database.playlist(playlist_id)
        if not playlist or playlist.is_favorites:
            return
        dialog = Adw.AlertDialog(
            heading="Delete Playlist?",
            body=f"“{playlist.name}” and its playlist entries will be removed. Audio files stay in your library.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._delete_playlist_response, playlist_id)
        dialog.present(self)

    def _delete_playlist_response(self, dialog, response, playlist_id):
        if response == "delete":
            self.database.delete_playlist(playlist_id)
            self._playlist_views.pop(playlist_id, None)
            self._refresh_playlist_sidebar()
            self._refresh_playlist_pages()
            self._show_page("library")
        dialog.close()

    def _player_bar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bar.add_css_class("player-bar")
        self.player_bar = bar
        self.mini_cover = cover_widget(None, 50)
        self.player_cover_slot = Gtk.Overlay()
        self.player_cover_slot.set_child(self.mini_cover)
        bar.append(self.player_cover_slot)
        meta = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            valign=Gtk.Align.CENTER,
            width_request=190,
        )
        self.bar_title = Gtk.Label(
            label="Nothing playing", xalign=0, ellipsize=3, css_classes=["player-title"]
        )
        self.bar_artist = Gtk.Label(
            label="Groovia", xalign=0, ellipsize=3, css_classes=["muted"]
        )
        meta.append(self.bar_title)
        meta.append(self.bar_artist)
        bar.append(meta)
        bar.append(
            icon_button(
                "media-skip-backward-symbolic", "Previous", lambda *_: self._previous()
            )
        )
        self.play_button = icon_button(
            "media-playback-start-symbolic", "Play", lambda *_: self.player.toggle()
        )
        self.play_button.add_css_class("circular")
        bar.append(self.play_button)
        bar.append(
            icon_button("media-skip-forward-symbolic", "Next", lambda *_: self._next())
        )
        self.repeat_button = icon_button(
            "media-playlist-repeat-symbolic",
            "Repeat all music",
            lambda *_: self._toggle_repeat(),
        )
        self.repeat_button.add_css_class("accent-button")
        bar.append(self.repeat_button)
        self.lyrics_button = icon_button(
            "text-x-generic-symbolic", "Show Lyrics", lambda *_: self._show_lyrics()
        )
        self.lyrics_button.set_sensitive(False)
        bar.append(self.lyrics_button)
        self.auto_dj_badge = Gtk.Label(label="Auto DJ", css_classes=["auto-dj-badge"])
        self.auto_dj_badge.set_visible(False)
        bar.append(self.auto_dj_badge)
        progress_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True
        )
        self.position_label = Gtk.Label(label="0:00", xalign=0, css_classes=["muted"])
        self.progress = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.1)
        self.progress.set_draw_value(False)
        self.progress.add_css_class("progress")
        self.progress.connect("change-value", self._on_seek)
        progress_box.append(self.progress)
        bar.append(progress_box)
        self.duration_label = Gtk.Label(label="0:00", css_classes=["muted"])
        bar.append(self.duration_label)
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.01)
        self.volume.set_value(0.72)
        self.volume.set_size_request(90, -1)
        self.volume.set_tooltip_text("Volume")
        self.volume.connect(
            "value-changed", lambda scale: self.player.set_volume(scale.get_value())
        )
        bar.append(Gtk.Image.new_from_icon_name("audio-volume-high-symbolic"))
        bar.append(self.volume)
        return bar

    def _connect_player(self):
        self.player.connect("track-changed", self._on_track_changed)
        self.player.connect("position-changed", self._on_position)
        self.player.connect("seeked", self._on_player_seeked)
        self.player.connect("state-changed", self._on_state)
        self.player.connect("track-transitioned", self._on_track_transitioned)
        self.player.connect(
            "auto-dj-transition-started", self._on_auto_dj_transition_started
        )
        self.player.connect(
            "auto-dj-transition-finished", self._on_auto_dj_transition_finished
        )
        self.player.connect("finished", lambda *_: self._next())
        self.player.connect("error", lambda _p, message: self._toast(message))

    def _on_auto_dj_transition_started(self, _player, _plan):
        if self._settings and not self._settings.get_boolean("auto-dj-show-badge"):
            return
        if hasattr(self, "auto_dj_badge"):
            self.auto_dj_badge.set_opacity(0.0)
            self.auto_dj_badge.set_visible(True)
            self.auto_dj_badge.set_opacity(1.0)

    def _on_auto_dj_transition_finished(self, _player, _previous, _next):
        if hasattr(self, "auto_dj_badge"):
            GLib.timeout_add(700, self._hide_auto_dj_badge)

    def _hide_auto_dj_badge(self):
        if hasattr(self, "auto_dj_badge"):
            self.auto_dj_badge.set_visible(False)
        return GLib.SOURCE_REMOVE

    def _refresh_library(self, search=""):
        tracks = self.database.all_tracks(search)
        for child in iter_gtk_children(self.library_content_box):
            self.library_content_box.remove(child)
        self.library_content_box.append(
            Gtk.Label(label="All Music", xalign=0, css_classes=["hero-title"])
        )
        self.library_content_box.append(
            Gtk.Label(
                label=f"{len(tracks)} tracks in your collection",
                xalign=0,
                css_classes=["muted"],
            )
        )
        self._library_tracks = tracks
        self._library_cursor = 0
        self.library_items_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        self.library_content_box.append(self.library_items_box)
        self._append_library_batch()
        for child in iter_gtk_children(self.album_flow):
            self.album_flow.remove(child)
        for album in self.database.albums():
            self.album_flow.append(self._album_card(album))
        for child in iter_gtk_children(self.recent_box):
            self.recent_box.remove(child)
        recent = self.database.recent_tracks()
        for track in recent:
            self.recent_box.append(self._track_row(track, False))
        self.empty_home.set_visible(not tracks)
        self._refresh_queue()
        self._refresh_playlist_sidebar()
        self._refresh_playlist_pages()

    def _append_library_batch(self):
        if self._library_loading or self._library_cursor >= len(self._library_tracks):
            return
        self._library_loading = True
        end = min(
            self._library_cursor + self._library_batch_size, len(self._library_tracks)
        )
        for track in self._library_tracks[self._library_cursor : end]:
            self.library_items_box.append(self._track_row(track, True))
        self._library_cursor = end
        self._library_loading = False

    def _on_library_scroll(self, adjustment):
        # Load the next batch before the user reaches the end, which feels
        # continuous while keeping widget creation bounded for large folders.
        if (
            adjustment.get_value() + adjustment.get_page_size()
            >= adjustment.get_upper() - 320
        ):
            self._append_library_batch()

    def _album_card(self, album):
        button = Gtk.Button()
        button.add_css_class("flat")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.add_css_class("album-card")
        box.append(cover_widget(album.get("cover_path"), 144))
        box.append(
            Gtk.Label(
                label=album["album"], xalign=0, ellipsize=3, css_classes=["album-title"]
            )
        )
        box.append(
            Gtk.Label(
                label=f'{album["album_artist"]} · {album["track_count"]} tracks',
                xalign=0,
                ellipsize=3,
                css_classes=["album-meta"],
            )
        )
        button.set_child(box)
        button.connect(
            "clicked",
            lambda *_: self._play_album(album["album"], album["album_artist"]),
        )
        return button

    def _track_row(
        self,
        track,
        show_cover=True,
        playlist: Playlist | None = None,
        position: int | None = None,
    ):
        row = Gtk.Box(spacing=12)
        box = row
        box.set_hexpand(True)
        box.add_css_class("track-row")
        box.set_focusable(True)
        box.set_accessible_role(Gtk.AccessibleRole.BUTTON)
        box.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"Play {track.title} by {track.artist}"],
        )
        box.set_tooltip_text(f"Play {track.title} by {track.artist}")
        if position is not None:
            box.append(
                Gtk.Label(
                    label=f"{position}.", width_chars=3, xalign=1, css_classes=["muted"]
                )
            )
        if show_cover:
            box.append(cover_widget(track.cover_path, 38))
        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        meta.append(
            Gtk.Label(
                label=track.title, xalign=0, ellipsize=3, css_classes=["player-title"]
            )
        )
        meta.append(
            Gtk.Label(
                label=track.subtitle, xalign=0, ellipsize=3, css_classes=["muted"]
            )
        )
        box.append(meta)
        box.append(self._favorite_button(track))
        box.append(Gtk.Label(label=track.duration_label, css_classes=["muted"]))
        box.append(
            icon_button(
                "media-playback-start-symbolic",
                "Play",
                lambda *_: self._play_selected_track(track, playlist),
            )
        )
        click = Gtk.GestureClick()
        click.set_button(0)
        click.connect("pressed", self._on_track_row_pressed, box, box, track, playlist)
        box.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect(
            "key-pressed", self._track_context_key_pressed, box, box, track, playlist
        )
        box.add_controller(keys)
        if playlist and track.id is not None:
            drag_source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
            drag_source.connect(
                "prepare",
                lambda _source, _x, _y, track_id=track.id: Gdk.ContentProvider.new_for_value(
                    track_id
                ),
            )
            box.add_controller(drag_source)
            drop_target = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)
            drop_target.set_preload(True)
            drop_target.connect(
                "drop",
                lambda _target, value, _x, _y, playlist=playlist, position=position: self._drop_playlist_track(
                    playlist, value, position
                ),
            )
            box.add_controller(drop_target)
        return row

    def _favorite_button(self, track):
        favorite = self.database.is_favorite(track)
        button = icon_button(
            "starred-symbolic",
            "Remove from Favorites" if favorite else "Add to Favorites",
            lambda *_: self._toggle_favorite(track),
        )
        if favorite:
            button.add_css_class("favorite-active")
        return button

    def _toggle_favorite(self, track):
        favorite = not self.database.is_favorite(track)
        self.database.set_favorite(track, favorite)
        LOGGER.info("favorite changed track=%r favorite=%s", track.path, favorite)
        self._toast(f"{'Added to' if favorite else 'Removed from'} Favorites")
        self._refresh_library(self.search_entry.get_text())

    def _on_track_row_pressed(self, gesture, n_press, x, y, row, box, track, playlist):
        button = gesture.get_current_button()
        LOGGER.info(
            "track row gesture button=%s presses=%s track=%r path=%r point=(%.1f, %.1f)",
            button,
            n_press,
            track.title,
            track.path,
            x,
            y,
        )
        if button == Gdk.BUTTON_PRIMARY and n_press == 1:
            self._play_selected_track(track, playlist)
        elif button == Gdk.BUTTON_SECONDARY:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._show_track_menu(row, box, track, x, y, playlist)

    def _track_context_key_pressed(
        self, _controller, keyval, _keycode, state, row, box, track, playlist
    ):
        menu_key = keyval in (Gdk.KEY_Menu, getattr(Gdk, "KEY_KP_Menu", Gdk.KEY_Menu))
        shift_f10 = keyval == Gdk.KEY_F10 and state & Gdk.ModifierType.SHIFT_MASK
        if playlist and state & Gdk.ModifierType.ALT_MASK:
            if keyval == Gdk.KEY_Up:
                self._move_playlist_track(playlist, track, -1)
                return True
            if keyval == Gdk.KEY_Down:
                self._move_playlist_track(playlist, track, 1)
                return True
        if menu_key or shift_f10:
            box.grab_focus()
            self._show_track_menu(row, box, track, 0, 0, playlist)
            return True
        return False

    def _show_track_menu(
        self, parent, source, track, x, y, playlist: Playlist | None = None
    ):
        if getattr(self, "_track_popover", None):
            self._track_popover.popdown()

        translated = source.translate_coordinates(parent, x, y)
        if translated:
            if len(translated) == 3:
                success, translated_x, translated_y = translated
                if success:
                    x, y = translated_x, translated_y
            else:
                x, y = translated
        LOGGER.info(
            "context menu open track=%r path=%r click=(%.1f, %.1f) anchor=(%d, %d, 1, 1)",
            track.title,
            track.path,
            x,
            y,
            round(x),
            round(y),
        )
        point = Gdk.Rectangle()
        point.x = round(x)
        point.y = round(y)
        point.width = 1
        point.height = 1

        callbacks = {
            "play": lambda: self._play_selected_track(track, playlist),
            "play-next": lambda: self._play_next(track),
            "add-to-queue": lambda: self._add_to_queue(track),
            "favorite": lambda: self._toggle_favorite(track),
            "go-to-album": lambda: self._go_to_album(track),
            "go-to-artist": lambda: self._go_to_artist(track),
            "show-in-file-manager": lambda: self._show_in_file_manager(track),
            "song-information": lambda: self._show_song_information(track),
            "show-lyrics": lambda: self._show_lyrics(track),
            "find-lyrics": lambda: self._find_lyrics_for_track(track),
            "import-lyrics": lambda: self._import_lyrics_for_track(track),
            "edit-lyrics": lambda: self._edit_lyrics(track),
            "remove-lyrics": lambda: self._confirm_remove_lyrics(track),
            "remove-from-library": lambda: self._confirm_remove_from_library(track),
        }
        lyrics_available = bool(self.download_service.lyrics.find(track)[0])

        popover = Gtk.Popover()
        popover.set_has_arrow(True)
        popover.set_parent(parent)
        popover.set_pointing_to(point)
        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu_box.set_margin_top(6)
        menu_box.set_margin_bottom(6)
        menu_box.set_margin_start(6)
        menu_box.set_margin_end(6)
        popover.set_child(menu_box)

        items = [
            ("play", "Play"),
            ("play-next", "Play Next"),
            ("add-to-queue", "Add to Queue"),
            (None, None),
            ("add-to-playlist", "Add to Playlist"),
            (
                "favorite",
                (
                    "Remove from Favorites"
                    if self.database.is_favorite(track)
                    else "Add to Favorites"
                ),
            ),
            (None, None),
            ("go-to-album", "Go to Album"),
            ("go-to-artist", "Go to Artist"),
            (None, None),
            ("show-in-file-manager", "Show in File Manager"),
            ("song-information", "Song Information"),
            (None, None),
            ("show-lyrics", "Show Lyrics"),
            ("find-lyrics", "Find Lyrics"),
            ("import-lyrics", "Import Lyrics"),
            ("edit-lyrics", "Edit Lyrics"),
            ("remove-lyrics", "Remove Downloaded Lyrics"),
            (None, None),
            ("remove-from-library", "Remove from Library"),
        ]
        if playlist:
            items.extend(
                [
                    (None, None),
                    ("remove-from-playlist", "Remove from Playlist"),
                    ("move-up", "Move Up in Playlist"),
                    ("move-down", "Move Down in Playlist"),
                ]
            )
        for name, label in items:
            if name is None:
                menu_box.append(Gtk.Separator())
                continue
            button = Gtk.Button(label=label, halign=Gtk.Align.FILL)
            button.add_css_class("flat")
            button.set_hexpand(True)
            button.set_halign(Gtk.Align.FILL)
            if name == "show-lyrics":
                button.set_sensitive(lyrics_available)
            if name == "add-to-playlist":
                button.connect(
                    "clicked",
                    lambda button: self._show_add_to_playlist_menu(track, button),
                )
            elif name == "remove-from-playlist":
                button.connect(
                    "clicked",
                    lambda _button: self._activate_track_action(
                        name,
                        track,
                        lambda: self._remove_track_from_playlist(track, playlist),
                    ),
                )
            elif name in ("move-up", "move-down"):
                direction = -1 if name == "move-up" else 1
                button.connect(
                    "clicked",
                    lambda _button, direction=direction: self._activate_track_action(
                        name,
                        track,
                        lambda: self._move_playlist_track(playlist, track, direction),
                    ),
                )
            else:
                button.connect(
                    "clicked",
                    lambda _button, name=name: self._activate_track_action(
                        name, track, callbacks[name]
                    ),
                )
            menu_box.append(button)

        popover.connect("closed", self._on_track_popover_closed)
        parent.connect(
            "notify::root", self._on_track_popover_parent_root_changed, popover
        )
        self._track_popover = popover
        popover.popup()

    def _show_add_to_playlist_menu(self, track, anchor):
        parent = anchor.get_parent()
        if parent is None:
            return
        if getattr(self, "_playlist_popover", None):
            self._playlist_popover.popdown()
        popover = Gtk.Popover()
        popover.set_parent(anchor)
        popover.set_has_arrow(True)
        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu_box.set_margin_top(6)
        menu_box.set_margin_bottom(6)
        menu_box.set_margin_start(6)
        menu_box.set_margin_end(6)
        popover.set_child(menu_box)
        existing = {
            playlist.id
            for playlist in self.database.playlists()
            if not playlist.is_favorites
        }
        for playlist in self.database.playlists():
            if playlist.is_favorites:
                continue
            button = Gtk.Button(label=playlist.name, halign=Gtk.Align.FILL)
            button.add_css_class("flat")
            button.connect(
                "clicked",
                lambda _button, pid=playlist.id: self._add_track_to_playlist(
                    track, pid, popover
                ),
            )
            menu_box.append(button)
        if existing:
            menu_box.append(Gtk.Separator())
        create = Gtk.Button(label="Create New Playlist", icon_name="list-add-symbolic")
        create.add_css_class("flat")
        create.connect(
            "clicked",
            lambda *_: (popover.popdown(), self._create_playlist_dialog(track)),
        )
        menu_box.append(create)
        popover.connect("closed", lambda current: self._close_playlist_popover(current))
        self._playlist_popover = popover
        popover.popup()

    def _close_playlist_popover(self, popover):
        if popover.get_parent() is not None:
            popover.unparent()
        if getattr(self, "_playlist_popover", None) is popover:
            self._playlist_popover = None

    def _add_track_to_playlist(self, track, playlist_id, popover=None):
        added = self.database.add_tracks_to_playlist(playlist_id, [track])
        if popover:
            popover.popdown()
        playlist = self.database.playlist(playlist_id)
        LOGGER.info(
            "add to playlist track=%r playlist=%r added=%s",
            track.path,
            playlist_id,
            added,
        )
        self._toast(
            f"Added to {playlist.name}"
            if added and playlist
            else "Track already in playlist"
        )
        self._refresh_playlist_sidebar()
        self._refresh_playlist_pages()

    def _remove_track_from_playlist(self, track, playlist):
        if not playlist or track.id is None:
            return
        self.database.remove_track_from_playlist(playlist.id, track.id)
        self._toast(f"Removed from {playlist.name}")
        self._refresh_playlist_page(playlist.id)

    def _move_playlist_track(self, playlist, track, direction):
        if not playlist or track.id is None:
            return
        ids = self.database.playlist_track_ids(playlist.id)
        try:
            index = ids.index(track.id)
        except ValueError:
            return
        target = index + direction
        if not 0 <= target < len(ids):
            return
        ids[index], ids[target] = ids[target], ids[index]
        self.database.reorder_playlist(playlist.id, ids)
        self._refresh_playlist_page(playlist.id)

    def _drop_playlist_track(self, playlist, track_id, position):
        if not playlist or playlist.is_favorites or not isinstance(track_id, int):
            return False
        ids = self.database.playlist_track_ids(playlist.id)
        if track_id not in ids:
            return False
        ids.remove(track_id)
        target = max(0, min(len(ids), int(position) - 1))
        ids.insert(target, track_id)
        self.database.reorder_playlist(playlist.id, ids)
        self._refresh_playlist_page(playlist.id)
        return True

    def _on_track_popover_closed(self, popover):
        parent = popover.get_parent()
        if parent is not None:
            popover.unparent()
        if getattr(self, "_track_popover", None) is popover:
            self._track_popover = None

    def _activate_track_action(self, name, track, callback):
        LOGGER.info(
            "context menu action start action=%s track=%r path=%r current=%r queue=%s",
            name,
            track.title,
            track.path,
            self.current.path if self.current else None,
            len(self.queue),
        )
        popover = getattr(self, "_track_popover", None)
        if popover is not None:
            popover.popdown()
        try:
            callback()
        except Exception:
            LOGGER.exception(
                "context menu action failed action=%s track=%r path=%r",
                name,
                track.title,
                track.path,
            )
            raise
        LOGGER.info(
            "context menu action done action=%s track=%r current=%r queue=%s",
            name,
            track.title,
            self.current.path if self.current else None,
            len(self.queue),
        )

    @staticmethod
    def _on_track_popover_parent_root_changed(parent, _pspec, popover):
        if parent.get_root() is None and popover.get_parent() is not None:
            popover.popdown()
            popover.unparent()

    def _refresh_queue(self):
        LOGGER.info("queue refresh size=%s", len(self.queue))
        for child in iter_gtk_children(self.queue_box):
            self.queue_box.remove(child)
        self.queue_empty.set_visible(not self.queue)
        for track in self.queue:
            self.queue_box.append(self._track_row(track, True))
        self.database.save_queue(self.queue)

    def _restore_playback(self):
        saved = self.database.load_playback()
        if not saved:
            return
        path, position = saved
        track = self.database.track_by_path(path)
        if not track:
            return

        # Recreate the playback source from the saved current track and the
        # pending queue so Next keeps the same order after a restart.
        self._playback_source = [track] + [
            item for item in self.queue if item.path != track.path
        ]
        self._play_track(track, autoplay=False)
        if position > 0:
            self.player.seek(min(position, max(0.0, self.player.duration)))
            self.database.save_playback(track, self.player.position)

    def _play_album(self, album, artist):
        tracks = [
            track
            for track in self.database.all_tracks()
            if track.album == album and track.album_artist == artist
        ]
        if tracks:
            self._library_random_mode = False
            self._history.clear()
            self._playback_source = tracks
            self.queue = tracks[1:]
            self._play_track(tracks[0])

    def _play_first(self):
        tracks = self.database.all_tracks()
        if tracks:
            self._current_playlist_id = None
            self._library_random_mode = True
            self._history.clear()
            self._playback_source = tracks
            self.queue = []
            self._play_track(tracks[0])

    def _play_track(self, track, autoplay=True):
        self._resolve_cover(track)
        if not self._playback_source:
            self._playback_source = self.database.all_tracks()
        self.current = track
        self.player.set_track(track, autoplay=autoplay)
        # set_track resets the secondary pipeline; prepare the transition
        # only after the new primary track is installed.
        self._prepare_next_track()
        self.database.mark_played(track)
        self.database.save_playback(track, 0.0)

    def _fill_random_library_queue(self):
        """Keep direct library playback supplied with random future tracks."""
        if not self._library_random_mode:
            return
        library = self.database.all_tracks()
        if not library:
            return
        current_path = self.current.path if self.current else None
        candidates = [track for track in library if track.path != current_path]
        if not candidates:
            candidates = library
        target_size = min(8, max(1, len(candidates)))
        queued_paths = {track.path for track in self.queue}
        while len(self.queue) < target_size:
            available = [track for track in candidates if track.path not in queued_paths]
            if not available:
                available = candidates
            track = random.choice(available)
            self.queue.append(track)
            queued_paths.add(track.path)

    def _prepare_next_track(self):
        """Keep the transition engine one track ahead of the visible queue."""
        self._fill_random_library_queue()
        candidate = None
        if self.queue:
            candidate = random.choice(self.queue) if self.shuffle else self.queue[0]
        elif self.repeat_mode == "all" and self._playback_source and self.current:
            current_index = next(
                (
                    i
                    for i, item in enumerate(self._playback_source)
                    if item.path == self.current.path
                ),
                -1,
            )
            if self.shuffle:
                candidates = [
                    item
                    for item in self._playback_source
                    if item.path != self.current.path
                ]
                candidate = random.choice(candidates) if candidates else self.current
            elif current_index >= 0 and len(self._playback_source) > 1:
                candidate = self._playback_source[
                    (current_index + 1) % len(self._playback_source)
                ]
            elif len(self._playback_source) == 1:
                candidate = self.current
        self.player.prepare_next(candidate)
        if (
            self._auto_dj_enabled
            and self.current
            and candidate
            and candidate.path != self.current.path
        ):
            self.auto_dj.prepare(self.current, candidate, self._auto_dj_options())
        else:
            self.auto_dj.cancel()
            self.player.set_auto_dj_plan(None)

    def _play_selected_track(self, track, playlist: Playlist | None = None):
        """Start a selected track with playlist order or random library mode."""
        LOGGER.info("play selected track=%r path=%r", track.title, track.path)
        if playlist:
            source = self.database.playlist_tracks(playlist.id)
        else:
            source = self.database.all_tracks()
        if not any(item.path == track.path for item in source):
            source = self.database.all_tracks()
        if playlist:
            self._current_playlist_id = playlist.id
            self._library_random_mode = False
        else:
            self._current_playlist_id = None
            self._library_random_mode = True
        self._playback_source = source
        selected_index = next(
            (i for i, item in enumerate(source) if item.path == track.path), -1
        )
        self._history = [] if self._library_random_mode else (
            source[:selected_index] if selected_index > 0 else []
        )
        self.queue = (
            [] if self._library_random_mode
            else source[selected_index + 1 :] if selected_index >= 0 else []
        )
        self._play_track(track)
        self._refresh_queue()

    def _play_next(self, track):
        """Put a track immediately after the currently playing track."""
        LOGGER.info(
            "play next before track=%r queue=%r",
            track.path,
            [item.path for item in self.queue],
        )
        self.queue.insert(0, track)
        self._prepare_next_track()
        self._refresh_queue()
        LOGGER.info(
            "play next after track=%r queue=%r",
            track.path,
            [item.path for item in self.queue],
        )

    def _add_to_queue(self, track):
        """Append a track, retaining duplicate queue entries."""
        LOGGER.info(
            "add to queue before track=%r queue=%r",
            track.path,
            [item.path for item in self.queue],
        )
        self.queue.append(track)
        self._prepare_next_track()
        self._refresh_queue()
        LOGGER.info(
            "add to queue after track=%r queue=%r",
            track.path,
            [item.path for item in self.queue],
        )

    def _go_to_album(self, track):
        LOGGER.info(
            "go to album track=%r album=%r album_artist=%r",
            track.path,
            track.album,
            track.album_artist,
        )
        album = track.album or "Unknown Album"
        tracks = [
            item
            for item in self.database.all_tracks()
            if item.album == track.album and item.album_artist == track.album_artist
        ]
        self._populate_collection(
            "album", album, track.album_artist or "Unknown Artist", tracks
        )

    def _go_to_artist(self, track):
        LOGGER.info(
            "go to artist track=%r artist=%r album_artist=%r",
            track.path,
            track.artist,
            track.album_artist,
        )
        artist = track.artist or track.album_artist or "Unknown Artist"
        tracks = [
            item
            for item in self.database.all_tracks()
            if item.artist == track.artist or item.album_artist == track.album_artist
        ]
        self._populate_collection("artist", artist, f"{len(tracks)} tracks", tracks)

    def _populate_collection(self, kind, title, subtitle, tracks):
        getattr(self, f"{kind}_detail_heading").set_label(title)
        getattr(self, f"{kind}_detail_subtitle").set_label(subtitle)
        items = getattr(self, f"{kind}_detail_items")
        for child in iter_gtk_children(items):
            items.remove(child)
        for item in tracks:
            items.append(self._track_row(item, True))
        self._show_page(f"{kind}-detail")

    def _show_in_file_manager(self, track):
        LOGGER.info("show in file manager track=%r path=%r", track.title, track.path)
        if not track.path or not Path(track.path).exists():
            LOGGER.warning(
                "file manager action skipped; file does not exist path=%r", track.path
            )
            self._toast("The audio file is no longer available")
            return
        if IS_WINDOWS:
            try:
                open_folder(Path(track.path).parent)
            except OSError as error:
                LOGGER.warning("Windows file manager launch failed: %s", error)
                self._toast("Could not open the file manager")
            return
        file = Gio.File.new_for_path(str(Path(track.path).resolve()))
        try:
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
                GLib.Variant("(ass)", ([file.get_uri()], "")),
                Gio.DBusCallFlags.NONE,
                1000,
                None,
            )
            LOGGER.info("file manager ShowItems succeeded uri=%r", file.get_uri())
            return
        except (GLib.Error, TypeError) as error:
            LOGGER.warning(
                "file manager ShowItems unavailable uri=%r error=%s",
                file.get_uri(),
                error,
            )

        parent = file.get_parent()
        if parent:
            try:
                Gio.AppInfo.launch_default_for_uri(parent.get_uri(), None)
                LOGGER.info("file manager fallback launched uri=%r", parent.get_uri())
            except GLib.Error as error:
                LOGGER.exception(
                    "file manager fallback failed uri=%r", parent.get_uri()
                )
                self._toast(f"Could not open the file manager: {error.message}")

    def _show_song_information(self, track):
        LOGGER.info(
            "song information requested track=%r path=%r", track.title, track.path
        )
        dialog = Gtk.Dialog(title="Song Information", transient_for=self, modal=True)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(520, 480)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)
        cover = cover_widget(track.cover_path, 180)
        cover.set_halign(Gtk.Align.CENTER)
        cover.set_margin_bottom(10)
        content.append(cover)
        technical = (
            self.scanner.inspect_track(track.path)
            if not track.path.startswith(("http://", "https://"))
            else {}
        )
        for label, value in (
            ("Title", track.title),
            ("Artist", track.artist),
            ("Album", track.album),
            ("Album artist", track.album_artist),
            ("Year", track.year),
            ("Genre", track.genre),
            ("Track", str(track.track_number)),
            ("Duration", track.duration_label),
            ("Codec", technical.get("codec", "Unknown")),
            ("Bitrate", technical.get("bitrate", "Unknown")),
            ("Sample rate", technical.get("sample_rate", "Unknown")),
            ("Channels", technical.get("channels", "Unknown")),
            ("File", track.path),
        ):
            line = Gtk.Box(spacing=12)
            line.append(Gtk.Label(label=f"{label}:", xalign=0, css_classes=["muted"]))
            line.append(
                Gtk.Label(
                    label=value or "—",
                    xalign=0,
                    wrap=True,
                    selectable=True,
                    hexpand=True,
                )
            )
            content.append(line)
        dialog.get_content_area().append(content)
        dialog.connect("response", lambda current, *_: current.close())
        dialog.present()

    def _find_lyrics_for_track(self, track):
        settings = self._settings
        providers = tuple(
            item.strip()
            for item in (
                settings.get_string("lyrics-providers")
                if settings
                else "synced,genius,musixmatch,azlyrics"
            ).split(",")
            if item.strip()
        )
        self.download_service.find_lyrics(track, providers=providers, fallback=True)
        self._toast("Searching for lyrics…")

    def _import_lyrics_for_track(self, track):
        chooser = Gtk.FileDialog(title="Import lyrics")
        chooser.open(
            self,
            None,
            lambda dialog, result: self._lyrics_file_selected(dialog, result, track),
        )

    def _edit_lyrics(self, track):
        timeline, row = self.download_service.lyrics.find(track)
        if not timeline:
            self._toast("No lyrics available to edit")
            return
        content = "\n".join(line.text for line in timeline.lines)
        if row and row.get("file_path"):
            try:
                content = Path(row["file_path"]).read_text(encoding="utf-8-sig")
            except OSError:
                pass
        dialog = Gtk.Dialog(title="Edit Lyrics", transient_for=self, modal=True)
        dialog.set_default_size(620, 520)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        save = dialog.add_button("Save", Gtk.ResponseType.OK)
        save.add_css_class("suggested-action")
        editor = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=timeline.synchronized
        )
        editor.get_buffer().set_text(content)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True, min_content_height=360)
        scroll.set_margin_top(18)
        scroll.set_margin_bottom(18)
        scroll.set_margin_start(18)
        scroll.set_margin_end(18)
        scroll.set_child(editor)
        dialog.get_content_area().append(scroll)

        def response(current, response_id):
            if response_id == Gtk.ResponseType.OK:
                buffer = editor.get_buffer()
                text = buffer.get_text(
                    buffer.get_start_iter(), buffer.get_end_iter(), False
                )
                if self.download_service.lyrics.save_text(
                    track, text, synchronized=timeline.synchronized
                ):
                    self._show_lyrics(track)
                    self._toast("Lyrics saved")
            current.close()

        dialog.connect("response", response)
        dialog.present()

    def _confirm_remove_lyrics(self, track):
        dialog = Adw.AlertDialog(
            heading="Remove downloaded lyrics?",
            body=f"Remove downloaded lyrics for “{track.title}”? Manually edited lyrics are kept.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(current, response_id):
            if response_id == "remove":
                self.download_service.lyrics.remove(track)
                self._show_lyrics(track)
            current.close()

        dialog.connect("response", response)
        dialog.present(self)

    def _confirm_remove_from_library(self, track):
        LOGGER.info(
            "remove confirmation opened track=%r path=%r", track.title, track.path
        )
        dialog = Adw.AlertDialog(
            heading="Remove from Library?",
            body=f"“{track.title}” will be removed from Groovia, but its audio file will not be deleted.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove from Library")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._remove_from_library_response, track)
        dialog.present(self)

    def _remove_from_library_response(self, dialog, response, track):
        LOGGER.info(
            "remove confirmation response=%s track=%r path=%r",
            response,
            track.title,
            track.path,
        )
        if response != "remove":
            return
        LOGGER.info(
            "removing track from library path=%r; file_exists=%s",
            track.path,
            Path(track.path).exists(),
        )
        self.database.remove_track(track.path)
        self.queue = [queued for queued in self.queue if queued.path != track.path]
        self._playback_source = [
            item for item in self._playback_source if item.path != track.path
        ]
        self._prepare_next_track()
        self._refresh_library(self.search_entry.get_text())
        self._toast(f"Removed {track.title} from the library")

    def _resolve_cover(self, track):
        """Backfill artwork for tracks imported before embedded-cover support."""
        if track.cover_path and Path(track.cover_path).exists():
            return
        try:
            refreshed = self.scanner.read_track(track.path)
            if refreshed.cover_path and Path(refreshed.cover_path).exists():
                track.cover_path = refreshed.cover_path
                self.database.upsert_tracks([track])
        except Exception:
            pass

    def _next(self):
        if self.repeat_mode == "one" and self.current:
            self._play_track(self.current)
            self._refresh_queue()
            return
        if self._auto_dj_enabled and self.current and self.queue and not self.shuffle:
            if (
                self.player.next_track
                and self.player.next_track.path == self.queue[0].path
                and self.player.start_prepared_transition(duration=1.8)
            ):
                self._history.append(self.current)
                self._refresh_queue()
                return
        if self.queue:
            index = random.randrange(len(self.queue)) if self.shuffle else 0
            if self.current:
                self._history.append(self.current)
            self._play_track(self.queue.pop(index))
        elif self._playback_source:
            current_index = next(
                (
                    i
                    for i, track in enumerate(self._playback_source)
                    if track.path == (self.current.path if self.current else "")
                ),
                -1,
            )
            if self.shuffle:
                candidates = [
                    i for i in range(len(self._playback_source)) if i != current_index
                ]
                next_index = random.choice(candidates) if candidates else current_index
            elif current_index + 1 < len(self._playback_source):
                next_index = current_index + 1
            elif self.repeat_mode == "all":
                next_index = 0
            else:
                self._toast("The queue is empty")
                self._refresh_queue()
                return
            if next_index >= 0:
                if self.current:
                    self._history.append(self.current)
                self._play_track(self._playback_source[next_index])
        else:
            self._toast("The queue is empty")
        self._refresh_queue()

    def _on_track_transitioned(self, _player, previous_track, next_track):
        """Commit an automatic crossfade as a normal queue transition."""
        if previous_track:
            self._history.append(previous_track)
        for index, track in enumerate(self.queue):
            if track.path == next_track.path:
                self.queue.pop(index)
                break
        self._prepare_next_track()
        self._refresh_queue()

    def _toggle_repeat(self):
        self.repeat_mode = {"all": "one", "one": "off", "off": "all"}[self.repeat_mode]
        self.repeat_all = self.repeat_mode == "all"
        if self.repeat_mode == "one":
            self.repeat_button.set_icon_name("media-playlist-repeat-song-symbolic")
            self.repeat_button.set_tooltip_text("Repeat one")
            self.repeat_button.set_opacity(1.0)
        elif self.repeat_mode == "all":
            self.repeat_button.set_icon_name("media-playlist-repeat-symbolic")
            self.repeat_button.set_tooltip_text("Repeat all music")
            self.repeat_button.set_opacity(1.0)
        else:
            self.repeat_button.set_icon_name("media-playlist-repeat-symbolic")
            self.repeat_button.set_tooltip_text("Repeat is off")
            self.repeat_button.set_opacity(0.45)
        self._apply_auto_dj_setting()
        self._prepare_next_track()
        self._sync_mpris()

    def _sync_mpris(self):
        service = getattr(self.get_application(), "mpris", None)
        if service and service.object:
            service.object.sync()

    def _previous(self):
        # Previous conventionally restarts the current track when it has
        # already played for a few seconds; only then walk backwards.
        if self.player.position > 3.0:
            self.player.seek(0)
            return

        if self._history:
            previous = self._history.pop()
            if self.current:
                self.queue.insert(0, self.current)
            self._play_track(previous)
            self._refresh_queue()
            return

        # Fallback for a freshly restored session where the in-memory history
        # is not available yet.
        if self._playback_source and self.current:
            current_index = next(
                (
                    i
                    for i, track in enumerate(self._playback_source)
                    if track.path == self.current.path
                ),
                -1,
            )
            if current_index > 0:
                self.queue.insert(0, self.current)
                self._play_track(self._playback_source[current_index - 1])
                self._refresh_queue()
                return
        self.player.seek(0)

    def _clear_queue(self):
        self.queue.clear()
        self._history.clear()
        self._prepare_next_track()
        self._refresh_queue()

    def _on_track_changed(self, _player, track):
        self.current = track
        self._resolve_cover(track)
        # Use the exact same path for the mini-cover, centre label and palette.
        cover_path = (
            track.cover_path
            if track.cover_path and Path(track.cover_path).exists()
            else None
        )
        self._set_album_palette(cover_path)
        self.now_title.set_label(track.title)
        self.now_artist.set_label(track.artist)
        self.now_album.set_label(track.album)
        self.bar_title.set_label(track.title)
        self.bar_artist.set_label(track.artist)
        # Replace only the artwork widget; the rest of the player bar remains stable.
        self._replace_player_cover(cover_path)
        self.vinyl.set_cover(cover_path)
        self.vinyl.set_progress(0)
        self.now_play.set_label("Pause")
        if hasattr(self, "lyrics_button"):
            timeline, _row = self.download_service.lyrics.find(track)
            self.lyrics_button.set_sensitive(bool(timeline))
        self._update_lyrics_for_current()
        if getattr(self, "_lyrics_fullscreen_window", None):
            self._lyrics_fullscreen_window.set_title(f"Lyrics — {track.title}")
            variants = self.download_service.lyrics.find_variants(track)
            self._lyrics_fullscreen_view.set_documents(variants, preferred_mode="line")
            self._set_fullscreen_lyrics_cover(track)
        self._notify_track(track, cover_path)

    def _notify_track(self, track, cover_path):
        application = self.get_application()
        if not application:
            return
        notification = Gio.Notification.new("Now playing")
        notification.set_body(f"{track.title} · {track.artist}")
        if cover_path:
            notification.set_icon(Gio.FileIcon.new(Gio.File.new_for_path(cover_path)))
        notification.set_default_action("app.activate")
        application.send_notification("now-playing", notification)

    def _replace_player_cover(self, cover_path):
        """Crossfade the small player artwork only for an Auto DJ handoff."""
        slot = getattr(self, "player_cover_slot", None)
        if slot is None:
            self.mini_cover = cover_widget(cover_path, 50)
            self.player_bar.append(self.mini_cover)
            return
        old_cover = self.mini_cover
        new_cover = cover_widget(cover_path, 50)
        animate = bool(
            self._auto_dj_enabled
            and self._settings
            and self._settings.get_boolean("auto-dj-artwork-animation")
        )
        gtk_settings = Gtk.Settings.get_default()
        animate = (
            animate
            and bool(
                gtk_settings is None
                or gtk_settings.get_property("gtk-enable-animations")
            )
            and (not self._settings or self._settings.get_boolean("animations"))
        )
        if not animate or old_cover is None or old_cover.get_parent() is not slot:
            slot.set_child(new_cover)
            self.mini_cover = new_cover
            return

        if getattr(self, "_cover_transition_animation", None):
            self._cover_transition_animation.skip()
        new_cover.set_opacity(0.0)
        slot.add_overlay(new_cover)
        settled = {"done": False}

        def update(value):
            progress = float(value)
            old_cover.set_opacity(1.0 - progress)
            new_cover.set_opacity(progress)
            if progress >= 0.999 and not settled["done"]:
                settled["done"] = True
                slot.remove_overlay(new_cover)
                slot.set_child(new_cover)
                self._cover_transition_animation = None

        animation = Adw.TimedAnimation.new(
            self, 0.0, 1.0, 420, Adw.CallbackAnimationTarget.new(update)
        )
        animation.set_easing(Adw.Easing.EASE_IN_OUT_CUBIC)
        self._cover_transition_animation = animation
        self.mini_cover = new_cover
        animation.play()

    def _on_position(self, _player, position, duration):
        self.position_label.set_label(self._time_label(position))
        self.duration_label.set_label(self._time_label(duration))
        self.progress.set_range(0, max(1, duration))
        self.progress.set_value(position)
        self.vinyl.set_duration(duration)
        self.vinyl.set_progress(position / duration if duration else 0)
        if (
            hasattr(self, "_lyrics_widgets")
            and self.stack.get_visible_child_name() == "lyrics"
        ):
            self._lyrics_widgets["view"].update_position(int(position * 1000))
        if getattr(self, "_lyrics_fullscreen_view", None):
            self._lyrics_fullscreen_view.update_position(int(position * 1000))

    def _on_player_seeked(self, _player, _position):
        position_ms = int(_position * 1000)
        if hasattr(self, "_lyrics_widgets"):
            view = self._lyrics_widgets["view"]
            if view.word_synchronized:
                view.update_position(position_ms)
        fullscreen_view = getattr(self, "_lyrics_fullscreen_view", None)
        if fullscreen_view is not None and fullscreen_view.word_synchronized:
            fullscreen_view.update_position(position_ms)
        if self._auto_dj_enabled and self.current:
            # A seek invalidates the old overlap position. Keep the same queue
            # candidate, but recreate its stream and its analysis plan.
            self._prepare_next_track()

    def _on_state(self, _player, playing):
        self.play_button.set_icon_name(
            "media-playback-pause-symbolic"
            if playing
            else "media-playback-start-symbolic"
        )
        self.play_button.set_tooltip_text("Pause" if playing else "Play")
        self.vinyl.set_playing(playing)
        self.now_play.set_icon_name(
            "media-playback-pause-symbolic"
            if playing
            else "media-playback-start-symbolic"
        )
        self.now_play.set_label("Pause" if playing else "Play")
        if not playing and self.current:
            self.database.save_playback(self.current, self.player.position)

    def _on_seek(self, _scale, _scroll, value):
        self.player.seek(value)
        return True

    def _on_vinyl_seek(self, _vinyl, seconds):
        if self.current:
            self.player.seek(seconds)
            self.database.save_playback(self.current, seconds)

    @staticmethod
    def _time_label(seconds):
        seconds = max(0, int(seconds))
        return f"{seconds // 60}:{seconds % 60:02d}"

    def _on_nav_selected(self, _list, row):
        if row:
            self._show_page(row.get_name())

    def _show_page(self, page):
        self.stack.set_visible_child_name(page)

    def _focus_search(self):
        self._show_page("library")
        self.search_entry.grab_focus()

    def _install_global_key_controller(self):
        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._global_key_pressed)
        self.add_controller(controller)

    def _global_key_pressed(self, _controller, keyval, _keycode, state):
        modifiers = (
            Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.ALT_MASK
            | Gdk.ModifierType.SHIFT_MASK
            | Gdk.ModifierType.SUPER_MASK
        )
        if (
            keyval == Gdk.KEY_space
            and not state & modifiers
            and not self._search_has_focus()
        ):
            self._toggle_play()
            return True
        return False

    def _search_has_focus(self) -> bool:
        """Keep the global Space shortcut from consuming search input."""
        focused = self.get_focus()
        while focused is not None:
            if focused is self.search_entry:
                return True
            focused = focused.get_parent()
        return False

    def _toggle_play(self):
        if self._search_has_focus():
            return
        if self.player.track:
            self.player.toggle()
        else:
            self._play_first()

    def _toggle_mute(self):
        self.player.set_volume(0 if self.player.volume else 0.72)
        self.volume.set_value(self.player.volume)

    def _choose_folder(self, *_args):
        dialog = Gtk.FileDialog(title="Choose a music folder")
        dialog.select_folder(self, None, self._folder_selected)

    def open_paths(self, paths: list[str]):
        """Open files from Nautilus, the desktop file, or a drag-and-drop."""
        tracks = []
        folders = []
        for raw_path in paths:
            path = Path(raw_path)
            if path.is_dir():
                folders.append(str(path))
            elif path.is_file() and path.suffix.lower() in FORMATS:
                try:
                    tracks.append(self.scanner.read_track(str(path)))
                except Exception:
                    continue
        if folders:
            self._toast("Scanning dropped music…")
            self.scanner.scan_async(folders, self._scan_update)
        if tracks:
            self.database.upsert_tracks(tracks)
            self._library_random_mode = False
            self._history.clear()
            self._playback_source = tracks
            self.queue = tracks[1:]
            self._play_track(tracks[0])
            self._refresh_library()

    def open_uri(self, uri: str):
        """Handle MPRIS OpenUri without bypassing the normal playback engine."""
        parsed = urlparse(uri)
        if parsed.scheme == "file":
            self.open_paths([unquote(parsed.path)])
            return
        if parsed.scheme in ("http", "https"):
            title = Path(unquote(parsed.path)).stem or uri
            track = Track(
                None,
                title,
                "Remote audio",
                "",
                "Remote audio",
                "",
                "",
                0,
                1,
                0,
                uri,
                None,
            )
            self.database.upsert_tracks([track])
            self._library_random_mode = False
            self._history.clear()
            self._playback_source = [track]
            self.queue = []
            self._play_track(track)

    def _download_url(self, *_args):
        dialog = Gtk.Dialog(title="Import from Spotify", transient_for=self, modal=True)
        dialog.set_default_size(620, 600)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        download_button = dialog.add_button("Download", Gtk.ResponseType.ACCEPT)
        download_button.add_css_class("suggested-action")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(22)
        body.set_margin_bottom(18)
        body.set_margin_start(22)
        body.set_margin_end(22)
        title = Gtk.Label(
            label="Import Spotify music", xalign=0, css_classes=["title-2"]
        )
        body.append(title)
        body.append(
            Gtk.Label(
                label="Groovia uses spotDL to find matching audio and import Spotify metadata and artwork."
                " Audio is not downloaded directly from Spotify; you are responsible for respecting copyright and service terms.",
                wrap=True,
                xalign=0,
                css_classes=["dim-label"],
            )
        )
        source_row = Gtk.Box(spacing=8)
        entry = Gtk.Entry(
            placeholder_text="Paste a Spotify track, playlist or .spotdl file",
            hexpand=True,
        )
        paste = Gtk.Button(label="Paste", icon_name="edit-paste-symbolic")
        paste.connect("clicked", lambda *_: self._paste_download_source(entry))
        source_row.append(entry)
        source_row.append(paste)
        body.append(source_row)
        detected = Gtk.Label(
            label="Paste a source to detect its type",
            xalign=0,
            css_classes=["dim-label"],
        )
        body.append(detected)
        destination = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"])
        body.append(destination)
        sync = Gtk.CheckButton(
            label="Keep this Spotify playlist synchronized in the future"
        )
        sync.set_active(True)
        sync.set_visible(False)
        body.append(sync)
        lyrics_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        lyrics_box.add_css_class("card")
        lyrics_box.set_margin_top(4)
        lyrics_box.append(Gtk.Label(label="Lyrics", xalign=0, css_classes=["heading"]))
        lyrics_synced = Gtk.CheckButton(label="Download synchronized lyrics")
        lyrics_fallback = Gtk.CheckButton(label="Use plain lyrics as fallback")
        lyrics_lrc = Gtk.CheckButton(label="Save an external .lrc file")
        lyrics_synced.set_active(
            self._settings.get_boolean("lyrics-synced") if self._settings else True
        )
        lyrics_fallback.set_active(
            self._settings.get_boolean("lyrics-fallback") if self._settings else True
        )
        lyrics_lrc.set_active(
            self._settings.get_boolean("lyrics-generate-lrc")
            if self._settings
            else True
        )
        lyrics_box.append(lyrics_synced)
        lyrics_box.append(lyrics_fallback)
        lyrics_box.append(lyrics_lrc)
        lyrics_box.append(
            Gtk.Label(
                label="Synchronized lyrics may not be available for every song. They follow playback when timing data exists.",
                wrap=True,
                xalign=0,
                css_classes=["dim-label"],
            )
        )
        body.append(lyrics_box)
        permission = Gtk.CheckButton(
            label="I understand and accept responsibility for this download"
        )
        if self._settings:
            permission.set_active(
                self._settings.get_boolean("spotdl-legal-acknowledged")
            )
        body.append(permission)
        progress = Gtk.ProgressBar(show_text=True)
        progress.set_text("Waiting for a source")
        body.append(progress)
        download_status = Gtk.Label(
            label="Waiting for a source", xalign=0, css_classes=["dim-label"]
        )
        body.append(download_status)
        current = Gtk.Label(label="", xalign=0, ellipsize=3, css_classes=["dim-label"])
        body.append(current)
        log_view = Gtk.TextView(
            editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR
        )
        log_view.set_vexpand(True)
        log_view.add_css_class("card")
        log_scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=130)
        log_scroll.set_child(log_view)
        body.append(log_scroll)
        dialog.get_content_area().append(body)
        self._download_dialog = dialog
        self._download_progress = progress
        self._download_status = download_status
        self._download_current = current
        self._download_log = log_view.get_buffer()
        self._download_sync = sync
        self._download_source_entry = entry
        self._download_destination = destination
        self._download_lyrics_synced = lyrics_synced
        self._download_lyrics_fallback = lyrics_fallback
        self._download_lyrics_lrc = lyrics_lrc
        entry.connect(
            "changed",
            lambda current_entry: self._update_download_detection(
                current_entry,
                detected,
                destination,
                sync,
                download_button,
                permission,
            ),
        )
        permission.connect(
            "toggled",
            lambda check: self._update_download_button(entry, check, download_button),
        )
        dialog.connect("response", self._download_response, entry, permission, sync)
        dialog.present()

    def _paste_download_source(self, entry):
        clipboard = self.get_display().get_clipboard()
        clipboard.read_text_async(
            None,
            lambda current, result: self._paste_download_finished(
                current, result, entry
            ),
        )

    @staticmethod
    def _paste_download_finished(clipboard, result, entry):
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            return
        if text:
            entry.set_text(text.strip())

    def _update_download_detection(
        self, entry, detected, destination, sync, button, permission
    ):
        info = classify_input(entry.get_text())
        labels = {
            "track": "Spotify track detected",
            "playlist": "Spotify playlist detected",
            "album": "Spotify album detected",
            "sync": "spotDL synchronization file detected",
            "invalid": "Waiting for a valid Spotify source",
        }
        detected.set_label(labels[info.kind])
        sync.set_visible(info.kind in {"playlist", "album", "sync"})
        if info.kind == "track":
            destination.set_label(f"Destination: {self.download_service.music_dir}")
        elif info.kind in {"playlist", "album", "sync"}:
            destination.set_label(
                f"Managed synchronization directory: {self.download_service.sync_root}"
            )
        else:
            destination.set_label("")
        button.set_sensitive(info.kind != "invalid" and permission.get_active())

    def _update_download_button(self, entry, permission, button):
        button.set_sensitive(
            classify_input(entry.get_text()).kind != "invalid"
            and permission.get_active()
        )

    def _download_response(self, dialog, response, entry, permission, sync):
        if response == Gtk.ResponseType.CANCEL:
            if getattr(self, "_download_job", None):
                self.download_service.manager.cancel(self._download_job.id)
            dialog.close()
            return
        if response != Gtk.ResponseType.ACCEPT:
            return
        info = classify_input(entry.get_text())
        if info.kind == "invalid":
            self._download_error(
                "Enter a valid Spotify track, playlist or .spotdl file."
            )
            return
        if not permission.get_active():
            self._download_error(
                "Please acknowledge the legal notice before downloading."
            )
            return
        if self._settings:
            self._settings.set_boolean("spotdl-legal-acknowledged", True)
        status = self.download_service.manager.resolver.dependency_status()
        missing = [
            name
            for name, present in (("spotDL", status.spotdl), ("FFmpeg", status.ffmpeg))
            if not present
        ]
        if IS_WINDOWS and not status.deno:
            missing.append("Deno")
        if not status.deno:
            self._append_download_log(
                "Deno was not found; spotDL recommends it for reliable YouTube matching."
                if not IS_WINDOWS
                else "Bundled Deno was not found in this Windows installation."
            )
        if missing:
            if IS_WINDOWS:
                self._download_error(
                    "Bundled downloader tools are missing. Reinstall Groovia or rebuild the Windows package."
                )
                return
            self._show_dependency_dialog(
                missing,
                lambda: self._start_download(entry.get_text(), sync.get_active()),
            )
            return
        self._start_download(entry.get_text(), sync.get_active())

    def _start_download(self, value, sync_enabled=True, existing_action=None):
        self._append_download_log(f"Starting: {value}")
        progress = getattr(self, "_download_progress", None)
        if progress:
            progress.set_fraction(0)
            progress.set_text("Starting spotDL…")
        settings = self._settings
        synced_widget = getattr(self, "_download_lyrics_synced", None)
        fallback_widget = getattr(self, "_download_lyrics_fallback", None)
        lrc_widget = getattr(self, "_download_lyrics_lrc", None)
        synced = (
            synced_widget.get_active()
            if synced_widget
            else (settings.get_boolean("lyrics-synced") if settings else True)
        )
        fallback = (
            fallback_widget.get_active()
            if fallback_widget
            else (settings.get_boolean("lyrics-fallback") if settings else True)
        )
        generate_lrc = (
            lrc_widget.get_active()
            if lrc_widget
            else (settings.get_boolean("lyrics-generate-lrc") if settings else True)
        )
        lyrics_mode = "synced" if synced else ("plain" if fallback else "none")
        provider_text = (
            settings.get_string("lyrics-providers")
            if settings
            else "synced,genius,musixmatch,azlyrics"
        )
        providers = tuple(
            item.strip() for item in provider_text.split(",") if item.strip()
        )
        job = self.download_service.submit(
            value,
            sync_enabled=sync_enabled,
            sync_mode=settings.get_string("sync-mode") if settings else "safe",
            existing_action=existing_action,
            output_format=settings.get_string("download-format") if settings else "mp3",
            bitrate=settings.get_string("download-bitrate") if settings else "auto",
            cover_policy=(
                settings.get_string("playlist-cover-policy") if settings else "follow"
            ),
            order_policy=(
                settings.get_string("playlist-order-policy") if settings else "spotify"
            ),
            lyrics_mode=lyrics_mode,
            lyrics_fallback=fallback,
            generate_lrc=generate_lrc,
            lyrics_providers=providers,
            sync_remove_lrc=(
                settings.get_boolean("lyrics-remove-sync") if settings else False
            ),
        )
        if job:
            self._download_job = job
            self._toast("Import started")

    def _download_event(self, event, job, payload):
        if event == "output":
            data = payload
            completed = data.get(
                "completed", getattr(job, "completed", 0) if job else 0
            )
            total = data.get("total", getattr(job, "total", 0) if job else 0)
            phase = data.get(
                "phase", getattr(job, "phase", "Downloading") if job else "Downloading"
            )
            failed = getattr(job, "failed", 0) if job else data.get("failed", 0)
            if data.get("progress") is not None and getattr(
                self, "_download_progress", None
            ):
                self._download_progress.set_fraction(data["progress"] / 100)
                progress_text = f"{data['progress']:.0f}%"
                if total:
                    progress_text += f" · {completed}/{total} tracks"
                self._download_progress.set_text(progress_text)
            elif total and getattr(self, "_download_progress", None):
                self._download_progress.set_fraction(min(1.0, completed / total))
                self._download_progress.set_text(f"{completed}/{total} tracks")
            if getattr(self, "_download_status", None):
                if total:
                    suffix = f" · {failed} failed" if failed else ""
                    self._download_status.set_label(
                        f"{phase} · Music {completed}/{total} downloaded{suffix}"
                    )
                else:
                    self._download_status.set_label(phase)
            if data.get("current") and getattr(self, "_download_current", None):
                self._download_current.set_label(data["current"])
            self._append_download_log(data.get("line", ""))
        elif event == "started":
            if getattr(self, "_download_status", None):
                self._download_status.set_label("Starting download…")
            self._append_download_log("spotDL process started")
        elif event == "command":
            if getattr(self, "_download_status", None):
                self._download_status.set_label("Preparing download…")
        elif event == "import-started":
            if getattr(self, "_download_progress", None):
                self._download_progress.set_text("Importing into library…")
            if getattr(self, "_download_status", None):
                self._download_status.set_label(
                    "Importing downloaded music into your library…"
                )
        elif event == "import-progress":
            current = payload.get("current", 0)
            total = payload.get("total", 0)
            title = payload.get("title") or ""
            phase = payload.get("phase", "Importing")
            if getattr(self, "_download_progress", None) and total:
                self._download_progress.set_fraction(min(1.0, current / total))
                self._download_progress.set_text(f"Library {current}/{total} tracks")
            if getattr(self, "_download_status", None):
                self._download_status.set_label(f"{phase} · {current}/{total} tracks")
            if title and getattr(self, "_download_current", None):
                self._download_current.set_label(title)
        elif event == "completed":
            if getattr(self, "_download_progress", None):
                self._download_progress.set_fraction(1)
                self._download_progress.set_text("Completed")
            if getattr(self, "_download_status", None):
                self._download_status.set_label(
                    f"Completed · {len(payload.get('tracks', []))} track(s) available in your library"
                )
            self._refresh_library(self.search_entry.get_text())
            tracks = payload.get("tracks", [])
            if payload.get("playlist"):
                self._toast(f"Playlist imported: {len(tracks)} tracks")
            else:
                self._toast("Track downloaded and added to your library")
            self._append_download_log(f"Completed: {len(tracks)} track(s) imported")
            lyrics_counts = payload.get("lyrics_counts", {})
            if lyrics_counts:
                self._append_download_log(
                    "Lyrics: "
                    f"{lyrics_counts.get('synced', 0)} synchronized, "
                    f"{lyrics_counts.get('plain', 0)} plain, "
                    f"{lyrics_counts.get('failed', 0)} unavailable"
                )
        elif event == "lyrics-completed":
            self._toast(
                "Lyrics downloaded" if payload.get("timeline") else "No lyrics found"
            )
            if payload.get("track"):
                self._show_lyrics(payload["track"])
        elif event == "lyrics-failed":
            self._toast(f"Lyrics unavailable: {payload.get('error', 'search failed')}")
        elif event in {"failed", "cancelled"}:
            message = payload.get("error") or (job.error if job else event)
            if payload.get("tracks"):
                self._refresh_library(self.search_entry.get_text())
                message = f"Import partially completed: {len(payload['tracks'])} track(s); {message}"
            self._download_error(
                "Download cancelled"
                if event == "cancelled"
                else f"Download failed: {message}"
            )
        elif event == "input-error":
            self._download_error(payload.get("message", "Invalid source"))
        elif event == "sync-error":
            self._download_error(
                payload.get("message", "Synchronization could not start")
            )
        elif event == "conflict":
            self._show_playlist_conflict(payload)
        elif event == "dependency-installed":
            message = (
                "Bundled downloader tools verified"
                if payload.get("bundled")
                else "Dependencies installed"
            )
            self._toast(message)
            self._append_download_log(message)
            dependency_dialog = getattr(self, "_dependency_dialog", None)
            if dependency_dialog:
                dependency_dialog.close()
                self._dependency_dialog = None
            resume = getattr(self, "_download_resume", None)
            self._download_resume = None
            if resume:
                resume()
        elif event == "dependency-verified":
            self._append_download_log("Downloader tool versions:")
            for name, result in payload.get("tools", {}).items():
                if result.get("available"):
                    self._append_download_log(f"{name}: {result.get('version', 'available')}")
                else:
                    self._append_download_log(f"{name}: unavailable ({result.get('error', 'unknown error')})")
            self._toast("Bundled downloader tools verified")
        elif event == "dependency-started":
            status = payload.get("status")
            self._set_dependency_feedback("Preparing dependency installation…")
            if status:
                self._append_download_log(
                    f"Dependencies: spotDL={'yes' if status.spotdl else 'no'}, "
                    f"FFmpeg={'yes' if status.ffmpeg else 'no'}, "
                    f"Deno={'yes' if status.deno else 'no'}"
                )
        elif event == "dependency-command":
            self._set_dependency_feedback(
                payload.get("label", "Installing dependency…")
            )
        elif event == "dependency-output":
            self._set_dependency_feedback(
                payload.get("label", "Installing dependency…"), pulse=True
            )
            self._append_dependency_log(payload.get("line", ""))
        elif event == "dependency-cancelled":
            self._set_dependency_feedback("Dependency installation cancelled")
            self._append_dependency_log(
                "Installation cancelled. No system packages were changed."
            )
        elif event == "dependency-failed":
            self._set_dependency_feedback("Dependency installation failed")
            self._append_dependency_log(
                payload.get("error", "Unknown installation error")
            )
            install_button = getattr(self, "_dependency_install_button", None)
            if install_button:
                install_button.set_sensitive(True)
            self._download_error(payload.get("error", "Dependency installation failed"))
        return GLib.SOURCE_REMOVE

    def _append_download_log(self, line):
        buffer = getattr(self, "_download_log", None)
        if buffer is None:
            return
        end = buffer.get_end_iter()
        # GTK 4 bindings require the explicit text length.  -1 means the
        # supplied UTF-8 string is NUL-terminated and keeps this compatible
        # with non-ASCII downloader output as well.
        buffer.insert(end, f"{line}\n", -1)

    def _download_error(self, message):
        self._append_download_log(f"ERROR: {message}")
        if getattr(self, "_download_progress", None):
            self._download_progress.set_text(message)
        self._toast(message)

    def _show_playlist_conflict(self, payload):
        playlist = payload["playlist"]
        dialog = Adw.AlertDialog(
            heading="Playlist already imported",
            body=f"{playlist.name} is already connected to this Spotify source.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("sync", "Synchronize existing")
        dialog.add_response("duplicate", "Import as new")
        dialog.add_response("replace", "Replace local playlist")
        dialog.set_default_response("sync")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            lambda current, response: self._conflict_response(
                current, response, payload
            ),
        )
        dialog.present(self)

    def _conflict_response(self, dialog, response, payload):
        dialog.close()
        if response in {"sync", "duplicate", "replace"}:
            self._start_download(
                payload["value"], self._download_sync.get_active(), response
            )

    def _show_dependency_dialog(self, missing, resume, presenter=None):
        if IS_WINDOWS:
            self._verify_download_tools(presenter)
            return
        dialog = Gtk.Dialog(
            title="Install download dependencies",
            transient_for=presenter or self,
            modal=True,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        install_button = dialog.add_button("Install", Gtk.ResponseType.ACCEPT)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.append(
            Gtk.Label(
                label="Groovia needs a few tools to import Spotify music.",
                xalign=0,
                css_classes=["title-3"],
            )
        )
        content.append(
            Gtk.Label(
                label="spotDL will be installed in Groovia's private environment. FFmpeg is required; Deno is recommended for reliable YouTube matching.",
                wrap=True,
                xalign=0,
                css_classes=["dim-label"],
            )
        )
        checks = {}
        for name in ("spotDL", "FFmpeg", "Deno"):
            check = Gtk.CheckButton(label=f"Install or repair {name}")
            check.set_active(name in missing)
            check.set_sensitive(name in missing or name != "spotDL")
            content.append(check)
            checks[name] = check
        feedback = Gtk.Label(
            label="Waiting for confirmation", xalign=0, css_classes=["dim-label"]
        )
        progress = Gtk.ProgressBar(show_text=True)
        progress.set_text("Waiting")
        log_view = Gtk.TextView(
            editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR
        )
        log_scroll = Gtk.ScrolledWindow(min_content_height=110, vexpand=True)
        log_scroll.set_child(log_view)
        content.append(feedback)
        content.append(progress)
        content.append(log_scroll)
        dialog.get_content_area().append(content)
        self._dependency_dialog = dialog
        self._dependency_install_button = install_button
        self._dependency_feedback = feedback
        self._dependency_progress = progress
        self._dependency_log = log_view.get_buffer()
        self._dependency_resume = resume
        dialog.connect("response", self._dependency_response, checks, resume)
        dialog.present()

    def _dependency_response(self, dialog, response, checks, resume):
        if response == Gtk.ResponseType.ACCEPT:
            self._download_resume = resume
            install_button = getattr(self, "_dependency_install_button", None)
            if install_button:
                install_button.set_sensitive(False)
            self._set_dependency_feedback("Starting installation…")
            self.download_service.manager.install_dependencies(
                checks["FFmpeg"].get_active(),
                checks["Deno"].get_active(),
                self._download_event,
                install_spotdl=checks["spotDL"].get_active(),
            )
            self._append_dependency_log("Installing selected dependencies…")
        else:
            if self.download_service.manager.cancel_dependency_installation():
                self._append_dependency_log("Stopping dependency installation…")
            self._download_resume = None
            self._dependency_dialog = None
            dialog.close()

    def _verify_download_tools(self, presenter=None):
        self._append_download_log("Verifying bundled downloader tools…")
        self.download_service.manager.verify_tools(self._download_event)
        self._toast("Verifying bundled downloader tools")

    def _set_dependency_feedback(self, message, pulse=False):
        feedback = getattr(self, "_dependency_feedback", None)
        if feedback:
            feedback.set_label(message)
        progress = getattr(self, "_dependency_progress", None)
        if progress:
            if pulse:
                progress.pulse()
            progress.set_text(message)

    def _append_dependency_log(self, line):
        buffer = getattr(self, "_dependency_log", None)
        if buffer is None:
            return
        end = buffer.get_end_iter()
        buffer.insert(end, f"{line}\n", -1)

    def _remove_managed_dependencies(self, presenter=None):
        if (
            self.download_service.manager.active
            or self.download_service.manager._dependency_process
        ):
            self._toast("Stop active downloads before removing managed tools")
            return
        dialog = Adw.AlertDialog(
            heading="Remove Groovia-managed download tools?",
            body=(
                "This removes only Groovia's private spotDL environment and its locally "
                "downloaded FFmpeg/Deno copies. System installations and your music files "
                "will not be touched."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(current, choice):
            current.close()
            if choice != "remove":
                return
            removed = self.download_service.manager.remove_managed_dependencies()
            self._toast(
                "Managed download tools removed"
                if removed
                else "No managed tools to remove"
            )

        dialog.connect("response", response)
        dialog.present(presenter or self)

    def _confirm_clear_all_data(self, presenter=None):
        manager = self.download_service.manager
        running_jobs = [
            job for job in manager.jobs() if job.state in {"queued", "running"}
        ]
        if running_jobs or manager._dependency_process:
            self._toast("Stop active downloads before deleting Groovia data")
            return

        data_root = self.download_service.data_root
        music_dir = self.download_service.music_dir
        cache_root = self.scanner.artwork_dir.parent
        dialog = Adw.AlertDialog(
            heading="Delete all Groovia data?",
            body=(
                "This permanently deletes Groovia's library database, downloaded music, lyrics, "
                "playlists, artwork, synchronization files, cache and managed download tools. "
                f"The music folder to delete is {music_dir}. Music imported from other folders will not be touched. "
                "Groovia will close after deletion."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete all data")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(current, choice):
            current.close()
            if choice == "delete":
                self._clear_all_data(data_root, music_dir, cache_root)

        dialog.connect("response", response)
        dialog.present(presenter or self)

    def _clear_all_data(self, data_root: Path, music_dir: Path, cache_root: Path):
        """Delete Groovia-owned files, then close so no state can be recreated."""
        self._data_reset = True
        self.current = None
        self.queue.clear()
        self._playback_source.clear()
        self._history.clear()
        self.player.close()
        self.auto_dj.close()

        for target in (data_root, music_dir, cache_root):
            path = Path(target).expanduser().resolve()
            if path in {Path("/"), Path.home().resolve()}:
                LOGGER.error("Refusing to delete unsafe Groovia data path: %s", path)
                self._data_reset = False
                self._toast("Could not delete Groovia data")
                return
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
            except OSError:
                LOGGER.exception("Could not delete Groovia data path: %s", path)
                self._data_reset = False
                self._toast("Could not delete all Groovia data")
                return

        if self._settings:
            for key in self._settings.list_keys():
                self._settings.reset(key)
        self.database.close()
        self.close()
        application = self.get_application()
        if application:
            application.quit()

    def _folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            self._toast("Scanning your music…")
            self.scanner.scan_async([folder.get_path()], self._scan_update)
        except GLib.Error:
            pass

    def _scan_update(self, state, current, total):
        if state == "finished":
            self._refresh_library()
            self._toast(f"Imported {current} tracks")
        return GLib.SOURCE_REMOVE if state == "finished" else GLib.SOURCE_CONTINUE

    def _toast(self, message):
        self.toast_overlay.add_toast(Adw.Toast(title=message, timeout=3))

    def close(self):
        if getattr(self, "_data_reset", False):
            super().close()
            return
        if getattr(self, "_lyrics_fullscreen_window", None):
            self._lyrics_fullscreen_window.close()
        popover = getattr(self, "_track_popover", None)
        if popover is not None:
            popover.popdown()
        self.database.save_queue(self.queue)
        self.database.save_playback(
            self.current, self.player.position if self.current else 0.0
        )
        self.auto_dj.close()
        self.player.close()
        self.database.close()
        super().close()
        if IS_WINDOWS:
            application = self.get_application()
            if application:
                application.quit()
