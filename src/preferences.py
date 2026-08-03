from gi.repository import Adw, Gio, Gtk


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, title="Preferences")
        settings = Gio.Settings.new("io.github.Lluciocc.Groovia")

        playback = Adw.PreferencesPage(title="Playback", icon_name="media-playback-start-symbolic")
        fade = Adw.ComboRow(title="Crossfade", subtitle="Mix the end of a track into the next one")
        fade.set_model(Gtk.StringList.new(["Off", "1 second", "3 seconds", "5 seconds", "8 seconds", "10 seconds"]))
        fade.set_selected(settings.get_int("crossfade-index"))
        fade.connect("notify::selected", lambda row, *_: settings.set_int("crossfade-index", row.get_selected()))
        playback.add(Adw.PreferencesGroup(title="Transitions", children=[fade]))
        options = Adw.PreferencesGroup(title="Options")
        gapless = Adw.SwitchRow(title="Gapless playback", subtitle="Avoid silence between compatible tracks")
        settings.bind("gapless", gapless, "active", Gio.SettingsBindFlags.DEFAULT)
        normalize = Adw.SwitchRow(title="ReplayGain", subtitle="Keep albums at a consistent listening level")
        settings.bind("replaygain", normalize, "active", Gio.SettingsBindFlags.DEFAULT)
        playback.add(options); options.add(gapless); options.add(normalize)

        interface = Adw.PreferencesPage(title="Interface", icon_name="applications-graphics-symbolic")
        visuals = Adw.PreferencesGroup(title="Visuals")
        dynamic = Adw.SwitchRow(title="Dynamic album background", subtitle="Tint the player with the current artwork")
        settings.bind("dynamic-background", dynamic, "active", Gio.SettingsBindFlags.DEFAULT)
        rotation = Adw.SwitchRow(title="Spinning vinyl", subtitle="Animate the record while music is playing")
        settings.bind("vinyl-rotation", rotation, "active", Gio.SettingsBindFlags.DEFAULT)
        animations = Adw.SwitchRow(title="Animations", subtitle="Respect the system reduced-motion preference")
        settings.bind("animations", animations, "active", Gio.SettingsBindFlags.DEFAULT)
        visuals.add(dynamic); visuals.add(rotation); visuals.add(animations); interface.add(visuals)

        library = Adw.PreferencesPage(title="Library", icon_name="folder-music-symbolic")
        group = Adw.PreferencesGroup(title="Music folders")
        group.add(Adw.ActionRow(title="Folders are scanned in the background", subtitle="Use Import music folder from the sidebar to add a location"))
        library.add(group)
        self.add(playback); self.add(interface); self.add(library)
