import os
import random
import time
from urllib.parse import unquote, urlparse
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .audio import AudioPlayer
from .downloads import DownloadManager
from .library import LibraryDatabase, LibraryScanner
from .library.scanner import FORMATS
from .models import Track
from .visuals import css_rgb, mix, palette_for
from .widgets import VinylView


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
.player-bar { background: @headerbar_bg_color; border-top: 1px solid @window_fg_color; padding: 8px 18px; }
.player-title { font-weight: 700; }
.progress { min-width: 220px; }
.queue-badge { background: #ff725e; color: white; border-radius: 99px; padding: 2px 7px; }
.empty-state { padding: 80px 24px; }
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
        self.player = AudioPlayer()
        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect("notify::accent-color", self._on_system_style_changed)
        self.style_manager.connect("notify::dark", self._on_system_style_changed)
        self.queue: list[Track] = self.database.load_queue()
        self.repeat_all = True
        self.shuffle = False
        self._playback_source: list[Track] = []
        self._history: list[Track] = []
        self.current: Track | None = None
        self._settings = self._load_settings()
        self._apply_crossfade_setting()
        if self._settings:
            self._settings.connect("changed::crossfade-index", self._on_crossfade_setting_changed)
        self._palette_cache = {}
        self._palette = self._system_palette()
        self._palette_animation = 0
        self._apply_css()
        self._build_ui()
        self._connect_player()
        self._refresh_library()
        self._restore_playback()

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

    def _system_palette(self):
        rgba = self.style_manager.get_accent_color_rgba()
        accent = (rgba.red, rgba.green, rgba.blue)
        factor = .16 if self.style_manager.get_dark() else .72
        background = tuple(max(.035, value * factor) for value in accent)
        return accent, background

    def _on_system_style_changed(self, *_args):
        if not self.current or not self.current.cover_path or not Path(self.current.cover_path).exists():
            self._set_album_palette(None)

    def _apply_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(display, provider,
                                                   Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._dynamic_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, self._dynamic_provider,
                                                   Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
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
        self._dynamic_provider.load_from_data(css.encode())
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
        progress = min(1.0, (time.monotonic() - self._palette_started_at) / .72)
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
        toggle = icon_button("sidebar-show-symbolic", "Toggle navigation", lambda *_: self.split.set_show_sidebar(not self.split.get_show_sidebar()))
        header.pack_start(toggle)
        brand = Gtk.Label(label="Groovia")
        brand.add_css_class("title-2")
        header.set_title_widget(brand)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search your library")
        self.search_entry.set_width_chars(24)
        self.search_entry.connect("search-changed", lambda entry: self._refresh_library(entry.get_text()))
        header.pack_end(self.search_entry)
        header.pack_end(icon_button("view-list-symbolic", "Queue", lambda *_: self._show_page("queue")))
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
        for title, icon, page in (("Home", "go-home-symbolic", "home"),
                                  ("All Music", "audio-x-generic-symbolic", "library"),
                                  ("Queue", "view-list-symbolic", "queue")):
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
        spacer = Gtk.Box(vexpand=True)
        box.append(spacer)
        import_button = Gtk.Button(label="Import music folder", icon_name="folder-music-symbolic")
        import_button.add_css_class("suggested-action")
        import_button.set_margin_start(14); import_button.set_margin_end(14)
        import_button.set_margin_bottom(14)
        import_button.connect("clicked", self._choose_folder)
        box.append(import_button)
        return box

    def _home_page(self):
        root = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.add_css_class("hero")
        intro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        intro.append(Gtk.Label(label="YOUR MUSIC, YOUR SPACE", xalign=0, css_classes=["eyebrow"]))
        intro.append(Gtk.Label(label="Good evening", xalign=0, css_classes=["hero-title"]))
        intro.append(Gtk.Label(label="Put on a record and let the room change.", xalign=0, css_classes=["muted"]))
        actions = Gtk.Box(spacing=8, margin_top=18)
        imp = Gtk.Button(label="Import music", icon_name="folder-music-symbolic")
        imp.add_css_class("suggested-action"); imp.connect("clicked", self._choose_folder)
        actions.append(imp)
        download = Gtk.Button(label="Download from URL", icon_name="document-save-symbolic")
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
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.CENTER)
        details.append(Gtk.Label(label="NOW PLAYING", xalign=0, css_classes=["eyebrow"]))
        self.now_title = Gtk.Label(label="Choose an album to start listening", xalign=0, wrap=True)
        self.now_title.add_css_class("now-title")
        self.now_title.set_xalign(.5)
        details.append(self.now_title)
        self.now_artist = Gtk.Label(label="Your local library is ready when you are.", xalign=0, css_classes=["muted"])
        self.now_artist.set_xalign(.5)
        details.append(self.now_artist)
        self.now_album = Gtk.Label(label="", xalign=0, css_classes=["muted"])
        self.now_album.set_xalign(.5)
        details.append(self.now_album)
        self.now_play = Gtk.Button(label="Play something", icon_name="media-playback-start-symbolic", halign=Gtk.Align.CENTER)
        self.now_play.add_css_class("pill"); self.now_play.connect("clicked", lambda *_: self._toggle_play())
        details.append(self.now_play)
        now.append(details)
        content.append(now)

        content.append(Gtk.Label(label="Recently added", xalign=0, css_classes=["section-title"], margin_top=28))
        self.album_flow = Gtk.FlowBox(max_children_per_line=6, min_children_per_line=2, selection_mode=Gtk.SelectionMode.NONE,
                                      row_spacing=12, column_spacing=12)
        content.append(self.album_flow)
        content.append(Gtk.Label(label="Recently played", xalign=0, css_classes=["section-title"], margin_top=28))
        self.recent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        content.append(self.recent_box)
        empty = Gtk.Label(label="Import a folder to bring your records into Groovia.", css_classes=["muted", "empty-state"])
        self.empty_home = empty
        content.append(empty)
        root.set_child(content)
        return root

    def _library_page(self):
        root = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.library_scroll = root
        root.get_vadjustment().connect("value-changed", self._on_library_scroll)
        self.library_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.library_box.set_margin_top(28); self.library_box.set_margin_start(38); self.library_box.set_margin_end(38)
        self.library_items_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._library_tracks = []
        self._library_cursor = 0
        self._library_batch_size = 40
        self._library_loading = False
        root.set_child(self.library_box)
        return root

    def _queue_page(self):
        root = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(28); box.set_margin_start(38); box.set_margin_end(38)
        head = Gtk.Box()
        head.append(Gtk.Label(label="Queue", xalign=0, css_classes=["hero-title"], hexpand=True))
        clear = Gtk.Button(label="Clear", tooltip_text="Clear queue"); clear.connect("clicked", lambda *_: self._clear_queue())
        head.append(clear); box.append(head)
        self.queue_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.append(self.queue_box)
        self.queue_empty = Gtk.Label(label="Your queue is empty.", css_classes=["muted", "empty-state"])
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
        back = Gtk.Button(label="Back to All Music", icon_name="go-previous-symbolic", halign=Gtk.Align.START)
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

    def _player_bar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bar.add_css_class("player-bar")
        self.player_bar = bar
        self.mini_cover = cover_widget(None, 50); bar.append(self.mini_cover)
        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, width_request=190)
        self.bar_title = Gtk.Label(label="Nothing playing", xalign=0, ellipsize=3, css_classes=["player-title"])
        self.bar_artist = Gtk.Label(label="Groovia", xalign=0, ellipsize=3, css_classes=["muted"])
        meta.append(self.bar_title); meta.append(self.bar_artist); bar.append(meta)
        bar.append(icon_button("media-skip-backward-symbolic", "Previous", lambda *_: self._previous()))
        self.play_button = icon_button("media-playback-start-symbolic", "Play", lambda *_: self.player.toggle())
        self.play_button.add_css_class("circular"); bar.append(self.play_button)
        bar.append(icon_button("media-skip-forward-symbolic", "Next", lambda *_: self._next()))
        self.repeat_button = icon_button("media-playlist-repeat-symbolic", "Repeat all music", lambda *_: self._toggle_repeat())
        self.repeat_button.add_css_class("accent-button")
        bar.append(self.repeat_button)
        progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        self.position_label = Gtk.Label(label="0:00", xalign=0, css_classes=["muted"])
        self.progress = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, .1)
        self.progress.set_draw_value(False); self.progress.add_css_class("progress")
        self.progress.connect("change-value", self._on_seek)
        progress_box.append(self.progress); bar.append(progress_box)
        self.duration_label = Gtk.Label(label="0:00", css_classes=["muted"]); bar.append(self.duration_label)
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, .01)
        self.volume.set_value(.72); self.volume.set_size_request(90, -1); self.volume.set_tooltip_text("Volume")
        self.volume.connect("value-changed", lambda scale: self.player.set_volume(scale.get_value()))
        bar.append(Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")); bar.append(self.volume)
        return bar

    def _connect_player(self):
        self.player.connect("track-changed", self._on_track_changed)
        self.player.connect("position-changed", self._on_position)
        self.player.connect("state-changed", self._on_state)
        self.player.connect("track-transitioned", self._on_track_transitioned)
        self.player.connect("finished", lambda *_: self._next())
        self.player.connect("error", lambda _p, message: self._toast(message))

    def _refresh_library(self, search=""):
        tracks = self.database.all_tracks(search)
        for child in list(self.library_box): self.library_box.remove(child)
        self.library_box.append(Gtk.Label(label="All Music", xalign=0, css_classes=["hero-title"]))
        self.library_box.append(Gtk.Label(label=f"{len(tracks)} tracks in your collection", xalign=0, css_classes=["muted"]))
        self._library_tracks = tracks
        self._library_cursor = 0
        self.library_items_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.library_box.append(self.library_items_box)
        self._append_library_batch()
        for child in list(self.album_flow): self.album_flow.remove(child)
        for album in self.database.albums(): self.album_flow.append(self._album_card(album))
        for child in list(self.recent_box): self.recent_box.remove(child)
        recent = self.database.recent_tracks()
        for track in recent: self.recent_box.append(self._track_row(track, False))
        self.empty_home.set_visible(not tracks)
        self._refresh_queue()

    def _append_library_batch(self):
        if self._library_loading or self._library_cursor >= len(self._library_tracks):
            return
        self._library_loading = True
        end = min(self._library_cursor + self._library_batch_size, len(self._library_tracks))
        for track in self._library_tracks[self._library_cursor:end]:
            self.library_items_box.append(self._track_row(track, True))
        self._library_cursor = end
        self._library_loading = False

    def _on_library_scroll(self, adjustment):
        # Load the next batch before the user reaches the end, which feels
        # continuous while keeping widget creation bounded for large folders.
        if adjustment.get_value() + adjustment.get_page_size() >= adjustment.get_upper() - 320:
            self._append_library_batch()

    def _album_card(self, album):
        button = Gtk.Button()
        button.add_css_class("flat")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.add_css_class("album-card")
        box.append(cover_widget(album.get("cover_path"), 144))
        box.append(Gtk.Label(label=album["album"], xalign=0, ellipsize=3, css_classes=["album-title"]))
        box.append(Gtk.Label(label=f'{album["album_artist"]} · {album["track_count"]} tracks', xalign=0, ellipsize=3,
                             css_classes=["album-meta"]))
        button.set_child(box)
        button.connect("clicked", lambda *_: self._play_album(album["album"], album["album_artist"]))
        return button

    def _track_row(self, track, show_cover=True):
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
        if show_cover: box.append(cover_widget(track.cover_path, 38))
        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        meta.append(Gtk.Label(label=track.title, xalign=0, ellipsize=3, css_classes=["player-title"]))
        meta.append(Gtk.Label(label=track.subtitle, xalign=0, ellipsize=3, css_classes=["muted"]))
        box.append(meta)
        box.append(Gtk.Label(label=track.duration_label, css_classes=["muted"]))
        box.append(icon_button("media-playback-start-symbolic", "Play", lambda *_: self._play_selected_track(track)))
        click = Gtk.GestureClick()
        click.set_button(0)
        click.connect("pressed", self._on_track_row_pressed, box, box, track)
        box.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._track_context_key_pressed, box, box, track)
        box.add_controller(keys)
        return row

    def _on_track_row_pressed(self, gesture, n_press, x, y, row, box, track):
        button = gesture.get_current_button()
        if button == Gdk.BUTTON_PRIMARY and n_press == 1:
            self._play_selected_track(track)
        elif button == Gdk.BUTTON_SECONDARY:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._show_track_menu(row, box, track, x, y)

    def _track_context_key_pressed(self, _controller, keyval, _keycode, state, row, box, track):
        menu_key = keyval in (Gdk.KEY_Menu, getattr(Gdk, "KEY_KP_Menu", Gdk.KEY_Menu))
        shift_f10 = keyval == Gdk.KEY_F10 and state & Gdk.ModifierType.SHIFT_MASK
        if menu_key or shift_f10:
            box.grab_focus()
            self._show_track_menu(row, box, track, 0, 0)
            return True
        return False

    def _show_track_menu(self, parent, source, track, x, y):
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
        point = Gdk.Rectangle()
        point.x = round(x)
        point.y = round(y)
        point.width = 1
        point.height = 1

        model = Gio.Menu()
        model.append("Play", "track.play")
        model.append("Play Next", "track.play-next")
        model.append("Add to Queue", "track.add-to-queue")

        navigation = Gio.Menu()
        navigation.append("Go to Album", "track.go-to-album")
        navigation.append("Go to Artist", "track.go-to-artist")
        model.append_section(None, navigation)

        details = Gio.Menu()
        details.append("Show in File Manager", "track.show-in-file-manager")
        details.append("Song Information", "track.song-information")
        model.append_section(None, details)

        library = Gio.Menu()
        library.append("Remove from Library", "track.remove-from-library")
        model.append_section(None, library)

        actions = Gio.SimpleActionGroup()
        callbacks = {
            "play": lambda: self._play_selected_track(track),
            "play-next": lambda: self._play_next(track),
            "add-to-queue": lambda: self._add_to_queue(track),
            "go-to-album": lambda: self._go_to_album(track),
            "go-to-artist": lambda: self._go_to_artist(track),
            "show-in-file-manager": lambda: self._show_in_file_manager(track),
            "song-information": lambda: self._show_song_information(track),
            "remove-from-library": lambda: self._confirm_remove_from_library(track),
        }
        for name, callback in callbacks.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect(
                "activate",
                lambda _action, _parameter, callback=callback: self._activate_track_action(callback),
            )
            actions.add_action(action)

        popover = Gtk.PopoverMenu.new_from_model(model)
        popover.insert_action_group("track", actions)
        popover.set_has_arrow(True)
        popover.set_parent(parent)
        popover.set_pointing_to(point)
        popover.connect("closed", self._on_track_popover_closed)
        parent.connect("notify::root", self._on_track_popover_parent_root_changed, popover)
        self._track_popover = popover
        popover.popup()

    def _on_track_popover_closed(self, popover):
        if popover.get_parent() is not None:
            popover.unparent()
        if getattr(self, "_track_popover", None) is popover:
            self._track_popover = None

    def _activate_track_action(self, callback):
        popover = getattr(self, "_track_popover", None)
        if popover is not None:
            popover.popdown()
        callback()

    @staticmethod
    def _on_track_popover_parent_root_changed(parent, _pspec, popover):
        if parent.get_root() is None and popover.get_parent() is not None:
            popover.popdown()
            popover.unparent()

    def _refresh_queue(self):
        for child in list(self.queue_box): self.queue_box.remove(child)
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
        self._playback_source = [track] + [item for item in self.queue if item.path != track.path]
        self._play_track(track, autoplay=False)
        if position > 0:
            self.player.seek(min(position, max(0.0, self.player.duration)))
            self.database.save_playback(track, self.player.position)

    def _play_album(self, album, artist):
        tracks = [track for track in self.database.all_tracks() if track.album == album and track.album_artist == artist]
        if tracks:
            self._history.clear()
            self._playback_source = tracks
            self.queue = tracks[1:]
            self._play_track(tracks[0])

    def _play_first(self):
        tracks = self.database.all_tracks()
        if tracks:
            self._history.clear()
            self._playback_source = tracks
            self.queue = tracks[1:]
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
        self._show_page("home")

    def _prepare_next_track(self):
        """Keep the transition engine one track ahead of the visible queue."""
        candidate = None
        if self.queue:
            candidate = random.choice(self.queue) if self.shuffle else self.queue[0]
        elif self.repeat_all and self._playback_source and self.current:
            current_index = next((i for i, item in enumerate(self._playback_source)
                                  if item.path == self.current.path), -1)
            if self.shuffle:
                candidates = [item for item in self._playback_source if item.path != self.current.path]
                candidate = random.choice(candidates) if candidates else self.current
            elif current_index >= 0 and len(self._playback_source) > 1:
                candidate = self._playback_source[(current_index + 1) % len(self._playback_source)]
            elif len(self._playback_source) == 1:
                candidate = self.current
        self.player.prepare_next(candidate)

    def _play_selected_track(self, track):
        """Start a track selected from the library while keeping Next useful.

        A direct library click used to leave an empty queue even though the
        whole library was available as the playback source. Rebuild the
        pending queue around the selected track, preserving the same source
        when possible.
        """
        source = self._playback_source or self.database.all_tracks()
        if not any(item.path == track.path for item in source):
            source = self.database.all_tracks()
        self._playback_source = source
        selected_index = next((i for i, item in enumerate(source) if item.path == track.path), -1)
        self._history = source[:selected_index] if selected_index > 0 else []
        self.queue = source[selected_index + 1:] if selected_index >= 0 else []
        self._play_track(track)
        self._refresh_queue()

    def _play_next(self, track):
        """Put a track immediately after the currently playing track."""
        self.queue.insert(0, track)
        self._prepare_next_track()
        self._refresh_queue()

    def _add_to_queue(self, track):
        """Append a track, retaining duplicate queue entries."""
        self.queue.append(track)
        self._prepare_next_track()
        self._refresh_queue()

    def _go_to_album(self, track):
        album = track.album or "Unknown Album"
        tracks = [item for item in self.database.all_tracks()
                  if item.album == track.album and item.album_artist == track.album_artist]
        self._populate_collection("album", album, track.album_artist or "Unknown Artist", tracks)

    def _go_to_artist(self, track):
        artist = track.artist or track.album_artist or "Unknown Artist"
        tracks = [item for item in self.database.all_tracks()
                  if item.artist == track.artist or item.album_artist == track.album_artist]
        self._populate_collection("artist", artist, f"{len(tracks)} tracks", tracks)

    def _populate_collection(self, kind, title, subtitle, tracks):
        getattr(self, f"{kind}_detail_heading").set_label(title)
        getattr(self, f"{kind}_detail_subtitle").set_label(subtitle)
        items = getattr(self, f"{kind}_detail_items")
        for child in list(items):
            items.remove(child)
        for item in tracks:
            items.append(self._track_row(item, True))
        self._show_page(f"{kind}-detail")

    def _show_in_file_manager(self, track):
        if not track.path or not Path(track.path).exists():
            self._toast("The audio file is no longer available")
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
            return
        except (GLib.Error, TypeError):
            pass

        parent = file.get_parent()
        if parent:
            try:
                Gio.AppInfo.launch_default_for_uri(parent.get_uri(), None)
            except GLib.Error as error:
                self._toast(f"Could not open the file manager: {error.message}")

    def _show_song_information(self, track):
        dialog = Gtk.Dialog(title="Song Information", transient_for=self, modal=True)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(520, 480)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)
        technical = self.scanner.inspect_track(track.path) if not track.path.startswith(("http://", "https://")) else {}
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
            line.append(Gtk.Label(label=value or "—", xalign=0, wrap=True, selectable=True, hexpand=True))
            content.append(line)
        dialog.get_content_area().append(content)
        dialog.connect("response", lambda current, *_: current.close())
        dialog.present()

    def _confirm_remove_from_library(self, track):
        dialog = Adw.AlertDialog(
            heading="Remove from Library?",
            body=f'“{track.title}” will be removed from Groovia, but its audio file will not be deleted.',
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove from Library")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._remove_from_library_response, track)
        dialog.present(self)

    def _remove_from_library_response(self, dialog, response, track):
        if response != "remove":
            return
        self.database.remove_track(track.path)
        self.queue = [queued for queued in self.queue if queued.path != track.path]
        self._playback_source = [item for item in self._playback_source if item.path != track.path]
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
        if self.queue:
            index = random.randrange(len(self.queue)) if self.shuffle else 0
            if self.current:
                self._history.append(self.current)
            self._play_track(self.queue.pop(index))
        elif self._playback_source:
            current_index = next((i for i, track in enumerate(self._playback_source)
                                  if track.path == (self.current.path if self.current else "")), -1)
            if self.shuffle:
                candidates = [i for i in range(len(self._playback_source)) if i != current_index]
                next_index = random.choice(candidates) if candidates else current_index
            elif current_index + 1 < len(self._playback_source):
                next_index = current_index + 1
            elif self.repeat_all:
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
        self.repeat_all = not self.repeat_all
        self.repeat_button.set_opacity(1.0 if self.repeat_all else .45)
        self.repeat_button.set_tooltip_text("Repeat all music" if self.repeat_all else "Repeat is off")
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
            current_index = next((i for i, track in enumerate(self._playback_source)
                                  if track.path == self.current.path), -1)
            if current_index > 0:
                self.queue.insert(0, self.current)
                self._play_track(self._playback_source[current_index - 1])
                self._refresh_queue()
                return
        self.player.seek(0)

    def _clear_queue(self):
        self.queue.clear(); self._history.clear(); self._prepare_next_track(); self._refresh_queue()

    def _on_track_changed(self, _player, track):
        self.current = track
        self._resolve_cover(track)
        # Use the exact same path for the mini-cover, centre label and palette.
        cover_path = track.cover_path if track.cover_path and Path(track.cover_path).exists() else None
        self._set_album_palette(cover_path)
        self.now_title.set_label(track.title); self.now_artist.set_label(track.artist); self.now_album.set_label(track.album)
        self.bar_title.set_label(track.title); self.bar_artist.set_label(track.artist)
        # Replace only the artwork widget; the rest of the player bar remains stable.
        if self.mini_cover.get_parent():
            self.player_bar.remove(self.mini_cover)
        self.mini_cover = cover_widget(cover_path, 50)
        self.player_bar.insert_child_after(self.mini_cover, None)
        self.vinyl.set_cover(cover_path); self.vinyl.set_progress(0)
        self.now_play.set_label("Pause")
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

    def _on_position(self, _player, position, duration):
        self.position_label.set_label(self._time_label(position)); self.duration_label.set_label(self._time_label(duration))
        self.progress.set_range(0, max(1, duration)); self.progress.set_value(position)
        self.vinyl.set_duration(duration)
        self.vinyl.set_progress(position / duration if duration else 0)

    def _on_state(self, _player, playing):
        self.play_button.set_icon_name("media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
        self.play_button.set_tooltip_text("Pause" if playing else "Play")
        self.vinyl.set_playing(playing)
        self.now_play.set_icon_name("media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
        self.now_play.set_label("Pause" if playing else "Play")
        if not playing and self.current:
            self.database.save_playback(self.current, self.player.position)

    def _on_seek(self, _scale, _scroll, value):
        self.player.seek(value); return True

    def _on_vinyl_seek(self, _vinyl, seconds):
        if self.current:
            self.player.seek(seconds)
            self.database.save_playback(self.current, seconds)

    @staticmethod
    def _time_label(seconds):
        seconds = max(0, int(seconds)); return f"{seconds // 60}:{seconds % 60:02d}"

    def _on_nav_selected(self, _list, row):
        if row: self._show_page(row.get_name())

    def _show_page(self, page):
        self.stack.set_visible_child_name(page)

    def _focus_search(self):
        self.search_entry.grab_focus()

    def _toggle_play(self):
        if self.player.track:
            self.player.toggle()
        else:
            self._play_first()

    def _toggle_mute(self):
        self.player.set_volume(0 if self.player.volume else .72)
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
            track = Track(None, title, "Remote audio", "", "Remote audio", "", "", 0, 1, 0, uri, None)
            self.database.upsert_tracks([track])
            self._history.clear()
            self._playback_source = [track]
            self.queue = []
            self._play_track(track)

    def _download_url(self, *_args):
        dialog = Gtk.Dialog(title="Download audio", transient_for=self, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Download", Gtk.ResponseType.ACCEPT)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(18); body.set_margin_bottom(18); body.set_margin_start(18); body.set_margin_end(18)
        body.append(Gtk.Label(label="Paste a direct audio URL from a source that permits downloading.", wrap=True, xalign=0))
        entry = Gtk.Entry(placeholder_text="https://example.org/track.flac")
        body.append(entry)
        permission = Gtk.CheckButton(label="I have permission to download this content")
        body.append(permission)
        dialog.get_content_area().append(body)
        dialog.connect("response", self._download_response, entry, permission)
        dialog.present()

    def _download_response(self, dialog, response, entry, permission):
        if response == Gtk.ResponseType.ACCEPT and permission.get_active() and entry.get_text().startswith(("http://", "https://")):
            self._toast("Downloading audio…")
            DownloadManager(self._download_update).download(entry.get_text().strip())
        dialog.close()

    def _download_update(self, state, value, total):
        if state == "finished":
            self._toast("Download finished — scan the file into your library")
            self.scanner.scan_async([str(Path(value).parent)], self._scan_update)
        elif state == "error":
            self._toast(f"Download failed: {value}")
        return GLib.SOURCE_REMOVE if state != "progress" else GLib.SOURCE_CONTINUE

    def _folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            self._toast("Scanning your music…")
            self.scanner.scan_async([folder.get_path()], self._scan_update)
        except GLib.Error:
            pass

    def _scan_update(self, state, current, total):
        if state == "finished":
            self._refresh_library(); self._toast(f"Imported {current} tracks")
        return GLib.SOURCE_REMOVE if state == "finished" else GLib.SOURCE_CONTINUE

    def _toast(self, message):
        self.toast_overlay.add_toast(Adw.Toast(title=message, timeout=3))

    def close(self):
        popover = getattr(self, "_track_popover", None)
        if popover is not None:
            popover.popdown()
        self.database.save_queue(self.queue)
        self.database.save_playback(self.current, self.player.position if self.current else 0.0)
        self.player.close(); self.database.close(); super().close()
