"""MPRIS 2 service implemented with Gio.DBus, without an extra sandbox dependency."""

import os
import time
from urllib.parse import quote

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

BUS_NAME = "org.mpris.MediaPlayer2.groovia"
OBJECT_PATH = "/org/mpris/MediaPlayer2"
ROOT_INTERFACE = "org.mpris.MediaPlayer2"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"

INTROSPECTION_XML = """<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/><method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Play"/><method name="Pause"/><method name="PlayPause"/><method name="Stop"/>
    <method name="Next"/><method name="Previous"/>
    <method name="Seek"><arg name="Offset" direction="in" type="x"/></method>
    <method name="SetPosition"><arg name="TrackId" direction="in" type="o"/><arg name="Position" direction="in" type="x"/></method>
    <method name="OpenUri"><arg name="Uri" direction="in" type="s"/></method>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="readwrite"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Shuffle" type="b" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>"""


class MprisService:
    """Own one user-bus name and expose the two standard MPRIS interfaces."""

    def __init__(self, window):
        self.window = window
        # Keep compatibility with the window's existing MPRIS refresh hook.
        # The service itself is the exported object; no duplicate playback
        # state or proxy object is needed.
        self.object = self
        self.connection = None
        self.owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            self._on_name_acquired,
            self._on_name_lost,
        )
        self.registration_ids = []
        self._last_position_signal = 0.0
        window.player.connect("track-changed", lambda *_: self.sync())
        window.player.connect("state-changed", lambda *_: self.sync())
        window.player.connect("position-changed", lambda *_: self.sync_position())
        window.player.connect(
            "volume-changed", lambda *_: self.sync_properties(["Volume"])
        )

    def _on_bus_acquired(self, connection, _name):
        self.connection = connection
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        for interface in node.interfaces:
            registration_id = connection.register_object_with_closures2(
                OBJECT_PATH,
                interface,
                self._method_call,
                self._get_property,
                self._set_property,
            )
            self.registration_ids.append(registration_id)

    def _on_name_acquired(self, _connection, name):
        if name != BUS_NAME:
            return

    def _on_name_lost(self, _connection, _name):
        self.connection = None

    def _method_call(
        self, _connection, _sender, _path, interface, method, parameters, invocation
    ):
        try:
            if interface == ROOT_INTERFACE:
                if method == "Raise":
                    self.window.present()
                elif method == "Quit":
                    self.window.get_application().quit()
                else:
                    return self._not_supported(invocation, method)
            elif interface == PLAYER_INTERFACE:
                args = parameters.unpack()
                if method == "Play":
                    self.window.player.play()
                elif method == "Pause":
                    self.window.player.pause()
                elif method == "PlayPause":
                    self.window.player.toggle()
                elif method == "Stop":
                    self.window.player.pause()
                    self.window.player.seek(0)
                elif method == "Next":
                    self.window._next()
                elif method == "Previous":
                    self.window._previous()
                elif method == "Seek":
                    self.window.player.seek(
                        self.window.player.position + args[0] / 1_000_000
                    )
                elif method == "SetPosition":
                    self.window.player.seek(args[1] / 1_000_000)
                elif method == "OpenUri":
                    self.window.open_uri(args[0])
                else:
                    return self._not_supported(invocation, method)
            invocation.return_value(GLib.Variant("()", ()))
        except Exception as error:
            invocation.return_dbus_error(
                "org.mpris.MediaPlayer2.Error.Failed", str(error)
            )

    @staticmethod
    def _not_supported(invocation, method):
        invocation.return_dbus_error(
            "org.mpris.MediaPlayer2.Error.NotSupported", method
        )

    def _get_property(self, _connection, _sender, _path, interface, prop):
        if interface == ROOT_INTERFACE:
            values = {
                "CanQuit": GLib.Variant("b", True),
                "CanRaise": GLib.Variant("b", True),
                "HasTrackList": GLib.Variant("b", False),
                "Identity": GLib.Variant("s", "Groovia"),
                "DesktopEntry": GLib.Variant("s", "io.github.Lluciocc.Groovia"),
                "SupportedUriSchemes": GLib.Variant("as", ["file", "http", "https"]),
                "SupportedMimeTypes": GLib.Variant(
                    "as", ["audio/mpeg", "audio/flac", "audio/ogg", "audio/mp4"]
                ),
            }
        else:
            player = self.window.player
            values = {
                "PlaybackStatus": GLib.Variant(
                    "s",
                    (
                        "Playing"
                        if player.playing
                        else ("Paused" if player.track else "Stopped")
                    ),
                ),
                "LoopStatus": GLib.Variant(
                    "s", "Playlist" if self.window.repeat_all else "None"
                ),
                "Rate": GLib.Variant("d", 1.0),
                "Shuffle": GLib.Variant("b", bool(self.window.shuffle)),
                "Metadata": GLib.Variant("a{sv}", self._metadata()),
                "Volume": GLib.Variant("d", player.volume),
                "Position": GLib.Variant("x", int(player.position * 1_000_000)),
                "MinimumRate": GLib.Variant("d", 1.0),
                "MaximumRate": GLib.Variant("d", 1.0),
                "CanGoNext": GLib.Variant(
                    "b", bool(self.window.queue or self.window.repeat_all)
                ),
                "CanGoPrevious": GLib.Variant("b", True),
                "CanPlay": GLib.Variant("b", True),
                "CanPause": GLib.Variant("b", True),
                "CanSeek": GLib.Variant("b", True),
                "CanControl": GLib.Variant("b", True),
            }
        return values.get(prop)

    def _set_property(self, _connection, _sender, _path, interface, prop, value):
        if interface != PLAYER_INTERFACE:
            return False
        if prop == "Volume":
            self.window.player.set_volume(value.unpack())
        elif prop == "LoopStatus":
            self.window.repeat_all = value.unpack() != "None"
            self.window._sync_mpris()
        elif prop == "Shuffle":
            self.window.shuffle = value.unpack()
            self.window._sync_mpris()
        else:
            return False
        self.sync_properties([prop])
        return True

    def _metadata(self):
        track = self.window.current
        if not track:
            return {}
        return {
            "mpris:trackid": GLib.Variant(
                "o",
                f"/org/mpris/MediaPlayer2/track/{track.id or abs(hash(track.path))}",
            ),
            "mpris:length": GLib.Variant("x", int(track.duration * 1_000_000)),
            "mpris:artUrl": GLib.Variant(
                "s", self._uri(track.cover_path) if track.cover_path else ""
            ),
            "xesam:title": GLib.Variant("s", track.title),
            "xesam:artist": GLib.Variant("as", [track.artist]),
            "xesam:album": GLib.Variant("s", track.album),
            "xesam:url": GLib.Variant("s", self._uri(track.path)),
        }

    def sync(self):
        self.sync_properties(
            ["PlaybackStatus", "Metadata", "CanGoNext", "LoopStatus", "Shuffle"]
        )

    def sync_position(self):
        now = time.monotonic()
        if now - self._last_position_signal >= 0.18:
            self._last_position_signal = now
            self.sync_properties(["Position"])

    def sync_properties(self, names):
        if not self.connection:
            return
        changed = {}
        for name in names:
            value = self._get_property(None, None, OBJECT_PATH, PLAYER_INTERFACE, name)
            if value is not None:
                changed[name] = value
        if changed:
            self.connection.emit_signal(
                None,
                OBJECT_PATH,
                "org.freedesktop.DBus.Properties",
                "PropertiesChanged",
                GLib.Variant("(sa{sv}as)", (PLAYER_INTERFACE, changed, [])),
            )

    @staticmethod
    def _uri(path):
        if path.startswith(("http://", "https://", "file://")):
            return path
        return "file://" + quote(os.path.abspath(path))

    def close(self):
        for registration_id in self.registration_ids:
            if self.connection:
                self.connection.unregister_object(registration_id)
        self.registration_ids.clear()
        Gio.bus_unown_name(self.owner_id)
        self.connection = None
        self.object = None
