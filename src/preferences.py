# preferences.py
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

from gi.repository import Adw, Gio, Gtk

from .platform_compat import IS_WINDOWS


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, title="Preferences")
        settings = Gio.Settings.new("io.github.Lluciocc.Groovia")

        playback = Adw.PreferencesPage(title="Playback", icon_name="media-playback-start-symbolic")
        fade = Adw.ComboRow(title="Crossfade", subtitle="Mix the end of a track into the next one")
        fade.set_model(
            Gtk.StringList.new(
                ["Off", "1 second", "3 seconds", "5 seconds", "8 seconds", "10 seconds"]
            )
        )
        fade.set_selected(settings.get_int("crossfade-index"))
        fade.connect(
            "notify::selected",
            lambda row, *_: settings.set_int("crossfade-index", row.get_selected()),
        )
        transitions = Adw.PreferencesGroup(title="Transitions")
        transitions.add(fade)
        playback.add(transitions)
        auto_dj_group = Adw.PreferencesGroup(
            title="Auto DJ",
            description="Analyze the next track in the background and create musical, queue-safe transitions.",
        )
        auto_dj = Adw.SwitchRow(
            title="Enable Auto DJ",
            subtitle="Use intelligent transitions while keeping the existing queue unchanged",
        )
        settings.bind("auto-dj-enabled", auto_dj, "active", Gio.SettingsBindFlags.DEFAULT)
        auto_dj_group.add(auto_dj)
        style = Adw.ComboRow(
            title="Transition style",
            subtitle="Choose how expressive the mix should feel",
        )
        style_values = ["subtle", "balanced", "energetic"]
        style.set_model(Gtk.StringList.new(["Subtle", "Balanced", "Energetic"]))
        style.set_selected(max(0, min(2, style_values.index(settings.get_string("auto-dj-style")))))
        style.connect(
            "notify::selected",
            lambda row, *_: settings.set_string("auto-dj-style", style_values[row.get_selected()]),
        )
        auto_dj_group.add(style)
        length = Adw.ComboRow(
            title="Transition length",
            subtitle="Automatic chooses a musical duration from the analysis",
        )
        length_values = ["automatic", "2", "4", "8", "12", "15"]
        length.set_model(
            Gtk.StringList.new(
                [
                    "Automatic",
                    "2 seconds",
                    "4 seconds",
                    "8 seconds",
                    "12 seconds",
                    "15 seconds",
                ]
            )
        )
        stored_length = settings.get_string("auto-dj-length")
        length.set_selected(
            length_values.index(stored_length) if stored_length in length_values else 0
        )
        length.connect(
            "notify::selected",
            lambda row, *_: settings.set_string(
                "auto-dj-length", length_values[row.get_selected()]
            ),
        )
        auto_dj_group.add(length)
        beat = Adw.SwitchRow(
            title="Beat matching", subtitle="Use BPM data only when confidence is high"
        )
        phrase = Adw.SwitchRow(
            title="Phrase matching",
            subtitle="Avoid switching in the middle of musical phrases",
        )
        tempo = Adw.SwitchRow(
            title="Tempo matching", subtitle="Never exceed the conservative tempo range"
        )
        smart_eq = Adw.SwitchRow(title="Smart EQ", subtitle="Reduce bass buildup during overlap")
        silence = Adw.SwitchRow(
            title="Silence detection",
            subtitle="Use intro and outro silence in the plan",
        )
        artwork = Adw.SwitchRow(
            title="Artwork animation",
            subtitle="Animate the player artwork during a transition",
        )
        badge = Adw.SwitchRow(
            title="Show Auto DJ badge",
            subtitle="Display a small indicator while tracks are mixed",
        )
        for key, row in (
            ("auto-dj-beat-matching", beat),
            ("auto-dj-phrase-matching", phrase),
            ("auto-dj-tempo-matching", tempo),
            ("auto-dj-smart-eq", smart_eq),
            ("auto-dj-silence-detection", silence),
            ("auto-dj-artwork-animation", artwork),
            ("auto-dj-show-badge", badge),
        ):
            settings.bind(key, row, "active", Gio.SettingsBindFlags.DEFAULT)
            auto_dj_group.add(row)
        playback.add(auto_dj_group)
        options = Adw.PreferencesGroup(title="Options")
        gapless = Adw.SwitchRow(
            title="Gapless playback", subtitle="Avoid silence between compatible tracks"
        )
        settings.bind("gapless", gapless, "active", Gio.SettingsBindFlags.DEFAULT)
        normalize = Adw.SwitchRow(
            title="ReplayGain", subtitle="Keep albums at a consistent listening level"
        )
        settings.bind("replaygain", normalize, "active", Gio.SettingsBindFlags.DEFAULT)
        playback.add(options)
        options.add(gapless)
        options.add(normalize)

        interface = Adw.PreferencesPage(
            title="Interface", icon_name="applications-graphics-symbolic"
        )
        visuals = Adw.PreferencesGroup(title="Visuals")
        dynamic = Adw.SwitchRow(
            title="Dynamic album background",
            subtitle="Tint the player with the current artwork",
        )
        settings.bind("dynamic-background", dynamic, "active", Gio.SettingsBindFlags.DEFAULT)
        sidebar_color = Adw.SwitchRow(
            title="Follow the app accent color",
            subtitle="Tint the sidebar with the accent generated from the current artwork",
        )
        settings.bind(
            "sidebar-dynamic-color",
            sidebar_color,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        rotation = Adw.SwitchRow(
            title="Spinning vinyl", subtitle="Animate the record while music is playing"
        )
        settings.bind("vinyl-rotation", rotation, "active", Gio.SettingsBindFlags.DEFAULT)
        animations = Adw.SwitchRow(
            title="Animations", subtitle="Respect the system reduced-motion preference"
        )
        settings.bind("animations", animations, "active", Gio.SettingsBindFlags.DEFAULT)
        lyrics_wave = Adw.SwitchRow(
            title="Lyrics wave",
            subtitle="Apply the soft vertical emphasis wave to word-synced lyrics",
        )
        settings.bind("lyrics-wave", lyrics_wave, "active", Gio.SettingsBindFlags.DEFAULT)
        lyrics_glow = Adw.SwitchRow(
            title="Lyrics glow",
            subtitle="Add a subtle light bloom around the current lyric position",
        )
        settings.bind("lyrics-glow", lyrics_glow, "active", Gio.SettingsBindFlags.DEFAULT)
        lyrics_background = Adw.ComboRow(
            title="Lyrics background",
            subtitle="Choose animated Better Lyrics artwork or the album cover",
        )
        lyrics_background.set_model(
            Gtk.StringList.new(["Animated artwork when available", "Album cover"])
        )
        background_values = ["animated", "cover"]
        stored_background = settings.get_string("lyrics-artwork-preference")
        lyrics_background.set_selected(
            background_values.index(stored_background)
            if stored_background in background_values
            else 0
        )
        lyrics_background.connect(
            "notify::selected",
            lambda row, *_: settings.set_string(
                "lyrics-artwork-preference", background_values[row.get_selected()]
            ),
        )
        visuals.add(dynamic)
        visuals.add(sidebar_color)
        visuals.add(rotation)
        visuals.add(animations)
        visuals.add(lyrics_wave)
        visuals.add(lyrics_glow)
        visuals.add(lyrics_background)
        interface.add(visuals)
        notifications = Adw.PreferencesGroup(title="Notifications")
        now_playing_notifications = Adw.SwitchRow(
            title="Now playing notifications",
            subtitle="Show a notification when the track changes",
        )
        settings.bind(
            "now-playing-notifications",
            now_playing_notifications,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        notifications.add(now_playing_notifications)
        interface.add(notifications)
        behavior = Adw.PreferencesGroup(title="Behavior")
        background_playback = Adw.SwitchRow(
            title="Keep Groovia running in the background",
            subtitle="Closing the window hides Groovia and keeps the music playing",
        )
        settings.bind(
            "background-playback",
            background_playback,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        behavior.add(background_playback)
        interface.add(behavior)

        library = Adw.PreferencesPage(title="Library", icon_name="folder-music-symbolic")
        group = Adw.PreferencesGroup(title="Music folders")
        group.add(
            Adw.ActionRow(
                title="Folders are scanned in the background",
                subtitle="Use Import music folder from the sidebar to add a location",
            )
        )
        library.add(group)

        downloads = Adw.PreferencesPage(title="Downloads", icon_name="document-save-symbolic")
        spotdl_group = Adw.PreferencesGroup(
            title="Spotify imports",
            description="Groovia uses spotDL to find matching audio and preserve Spotify metadata.",
        )
        dependency = Adw.ActionRow(
            title=("Bundled downloader tools" if IS_WINDOWS else "spotDL dependencies"),
            subtitle=(
                "spotDL, FFmpeg and Deno are installed with Groovia and managed by the installer."
                if IS_WINDOWS
                else "spotDL, FFmpeg and Deno are managed separately from playback."
            ),
        )
        repair = Gtk.Button(
            label=("Verify bundled tools" if IS_WINDOWS else "Install or repair"),
            valign=Gtk.Align.CENTER,
        )
        repair.add_css_class("suggested-action")
        if IS_WINDOWS:
            repair.connect("clicked", lambda *_: parent._verify_download_tools(self))
        else:
            repair.connect(
                "clicked",
                lambda *_: parent._show_dependency_dialog(
                    ["spotDL", "FFmpeg", "Deno"],
                    lambda: None,
                    presenter=self,
                ),
            )
        dependency.add_suffix(repair)
        spotdl_group.add(dependency)
        if IS_WINDOWS:
            spotdl_group.add(
                Adw.ActionRow(
                    title="Packaged tools",
                    subtitle="Reinstall Groovia to repair bundled downloader files.",
                )
            )
        else:
            remove_row = Adw.ActionRow(
                title="Remove Groovia-managed tools",
                subtitle="Remove the private spotDL environment, FFmpeg and Deno copies.",
            )
            remove = Gtk.Button(label="Remove", valign=Gtk.Align.CENTER)
            remove.add_css_class("destructive-action")
            remove.connect(
                "clicked", lambda *_: parent._remove_managed_dependencies(presenter=self)
            )
            remove_row.add_suffix(remove)
            spotdl_group.add(remove_row)
        downloads.add(spotdl_group)

        locations = Adw.PreferencesGroup(title="Locations")
        music_location = Adw.ActionRow(
            title="Music download directory",
            subtitle=str(parent.download_service.music_dir),
        )
        sync_location = Adw.ActionRow(
            title="Synchronized playlists",
            subtitle=str(parent.download_service.sync_root),
        )
        locations.add(music_location)
        locations.add(sync_location)
        downloads.add(locations)

        options = Adw.PreferencesGroup(title="Download options")
        format_row = Adw.ComboRow(title="Audio format")
        format_row.set_model(Gtk.StringList.new(["MP3", "FLAC", "M4A", "OPUS", "OGG"]))
        format_values = ["mp3", "flac", "m4a", "opus", "ogg"]
        format_row.set_selected(max(0, format_values.index(settings.get_string("download-format"))))
        format_row.connect(
            "notify::selected",
            lambda row, *_: settings.set_string(
                "download-format", format_values[row.get_selected()]
            ),
        )
        bitrate_row = Adw.ComboRow(title="Audio bitrate")
        bitrate_row.set_model(
            Gtk.StringList.new(["Automatic", "96 kbps", "128 kbps", "192 kbps", "320 kbps"])
        )
        bitrate_values = ["auto", "96k", "128k", "192k", "320k"]
        bitrate_row.set_selected(
            max(0, bitrate_values.index(settings.get_string("download-bitrate")))
        )
        bitrate_row.connect(
            "notify::selected",
            lambda row, *_: settings.set_string(
                "download-bitrate", bitrate_values[row.get_selected()]
            ),
        )
        options.add(format_row)
        options.add(bitrate_row)
        downloads.add(options)

        sync_options = Adw.PreferencesGroup(title="Synchronization")
        mode = Adw.ComboRow(
            title="Synchronization mode",
            subtitle="Safe mode never deletes local audio files.",
        )
        mode.set_model(Gtk.StringList.new(["Safe", "Mirror"]))
        mode_values = ["safe", "mirror"]
        mode.set_selected(max(0, mode_values.index(settings.get_string("sync-mode"))))
        mode.connect(
            "notify::selected",
            lambda row, *_: settings.set_string("sync-mode", mode_values[row.get_selected()]),
        )
        policy = Adw.ComboRow(title="Automatic synchronization")
        policy.set_model(
            Gtk.StringList.new(["Manually only", "On startup", "Once per day", "Once per week"])
        )
        policy_values = ["manual", "startup", "daily", "weekly"]
        policy.set_selected(max(0, policy_values.index(settings.get_string("auto-sync-policy"))))
        policy.connect(
            "notify::selected",
            lambda row, *_: settings.set_string(
                "auto-sync-policy", policy_values[row.get_selected()]
            ),
        )
        cover_policy = Adw.ComboRow(title="Playlist cover")
        cover_policy.set_model(Gtk.StringList.new(["Follow Spotify", "Keep custom local cover"]))
        cover_values = ["follow", "custom"]
        cover_policy.set_selected(
            max(0, cover_values.index(settings.get_string("playlist-cover-policy")))
        )
        cover_policy.connect(
            "notify::selected",
            lambda row, *_: settings.set_string(
                "playlist-cover-policy", cover_values[row.get_selected()]
            ),
        )
        order_policy = Adw.ComboRow(title="Playlist order")
        order_policy.set_model(Gtk.StringList.new(["Follow Spotify order", "Keep local order"]))
        order_values = ["spotify", "local"]
        order_policy.set_selected(
            max(0, order_values.index(settings.get_string("playlist-order-policy")))
        )
        order_policy.connect(
            "notify::selected",
            lambda row, *_: settings.set_string(
                "playlist-order-policy", order_values[row.get_selected()]
            ),
        )
        sync_options.add(mode)
        sync_options.add(policy)
        sync_options.add(cover_policy)
        sync_options.add(order_policy)
        downloads.add(sync_options)

        lyrics_options = Adw.PreferencesGroup(
            title="Lyrics",
            description="Synchronized lyrics are optional and may not be available for every track.",
        )
        fallback_lyrics = Adw.SwitchRow(title="Use LRCLIB when Better Lyrics is unavailable")
        settings.bind("lyrics-fallback", fallback_lyrics, "active", Gio.SettingsBindFlags.DEFAULT)
        auto_missing = Adw.SwitchRow(title="Search automatically for missing lyrics")
        settings.bind("lyrics-auto-missing", auto_missing, "active", Gio.SettingsBindFlags.DEFAULT)
        preserve = Adw.SwitchRow(title="Keep manually edited lyrics")
        settings.bind("lyrics-preserve-edited", preserve, "active", Gio.SettingsBindFlags.DEFAULT)
        show_availability = Adw.SwitchRow(title="Show lyrics availability")
        settings.bind(
            "lyrics-show-availability",
            show_availability,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        provider_info = Adw.ActionRow(
            title="Online lyrics providers",
            subtitle="Better Lyrics (syllable sync) with LRCLIB as a fallback",
        )
        for row in (
            fallback_lyrics,
            auto_missing,
            preserve,
            show_availability,
            provider_info,
        ):
            lyrics_options.add(row)
        downloads.add(lyrics_options)

        data = Adw.PreferencesPage(title="Data", icon_name="edit-delete-symbolic")
        data_group = Adw.PreferencesGroup(
            title="Reset Groovia",
            description="Remove Groovia's downloaded music, lyrics, playlists, cache and settings.",
        )
        clear_row = Adw.ActionRow(
            title="Delete all Groovia data",
            subtitle="Downloaded music in Music/Groovia and all local application data will be permanently deleted.",
        )
        clear_button = Gtk.Button(label="Delete all data", valign=Gtk.Align.CENTER)
        clear_button.add_css_class("destructive-action")
        clear_button.connect("clicked", lambda *_: parent._confirm_clear_all_data(presenter=self))
        clear_row.add_suffix(clear_button)
        data_group.add(clear_row)
        data.add(data_group)

        self.add(playback)
        self.add(interface)
        self.add(library)
        self.add(downloads)
        self.add(data)
