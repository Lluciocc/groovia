import math
import logging
import os

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, GObject, Gst


LOGGER = logging.getLogger("groovia.audio")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[Groovia audio] %(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


class AudioPlayer(GObject.Object):
    """GStreamer player with a second playbin used for real crossfades."""

    __gsignals__ = {
        "track-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "position-changed": (GObject.SignalFlags.RUN_FIRST, None, (float, float)),
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "volume-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "error": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "finished": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "track-transitioned": (GObject.SignalFlags.RUN_FIRST, None, (object, object)),
        "auto-dj-transition-started": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "auto-dj-transition-finished": (GObject.SignalFlags.RUN_FIRST, None, (object, object)),
    }

    def __init__(self):
        super().__init__()
        Gst.init(None)
        self.pipeline = None
        self.next_pipeline = None
        self.track = None
        self.next_track = None
        self.playing = False
        self.volume = 0.72
        self.position = 0.0
        self.duration = 0.0
        self.crossfade = 5.0
        self.auto_dj_enabled = False
        self.auto_dj_plan = None
        self._crossfading = False
        self._auto_dj_transition = False
        GLib.timeout_add(200, self._tick)

    def _new_pipeline(self, track, volume, auto_dj=False):
        pipeline = Gst.ElementFactory.make("playbin", None)
        if not pipeline:
            self.emit("error", "GStreamer playback is unavailable on this system.")
            return None
        pipeline.props.uri = track.path if track.path.startswith(("http://", "https://")) else Gst.filename_to_uri(os.path.abspath(track.path))
        pipeline.props.volume = volume
        if auto_dj:
            equalizer = Gst.ElementFactory.make("equalizer-3bands", None)
            if equalizer:
                pipeline.props.audio_filter = equalizer
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_message, pipeline)
        return pipeline

    def set_track(self, track, autoplay=True):
        if self._crossfading:
            LOGGER.info("Crossfade interrupted by manual track change")
        self._stop_pipeline(self.pipeline)
        self._stop_pipeline(self.next_pipeline)
        self.next_pipeline, self.next_track = None, None
        self._crossfading = False
        self.track = track
        self.position = 0.0
        self.duration = track.duration
        self.pipeline = self._new_pipeline(track, self.volume, self.auto_dj_enabled)
        if self.pipeline:
            self.pipeline.set_state(Gst.State.PLAYING if autoplay else Gst.State.PAUSED)
        self.playing = bool(autoplay)
        self.emit("track-changed", track)
        self.emit("state-changed", self.playing)

    def play(self):
        if self.pipeline:
            self.pipeline.set_state(Gst.State.PLAYING)
            self.playing = True
            self.emit("state-changed", True)

    def pause(self):
        if self._crossfading:
            self._cancel_crossfade("pause", preserve_next=True)
        if self.pipeline:
            self.pipeline.set_state(Gst.State.PAUSED)
            self.playing = False
            self.emit("state-changed", False)

    def toggle(self):
        self.pause() if self.playing else self.play()

    def seek(self, seconds):
        if self.pipeline:
            self.position = max(0.0, float(seconds))
            self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                                      max(0, int(seconds * Gst.SECOND)))
            self._cancel_crossfade("seek", preserve_next=True)

    def set_volume(self, value):
        self.volume = max(0.0, min(1.0, value))
        if self.pipeline and not self._crossfading:
            self.pipeline.props.volume = self.volume
        self.emit("volume-changed", self.volume)

    def _tick(self):
        if not self.pipeline or not self.track:
            return True
        try:
            position = self.pipeline.query_position(Gst.Format.TIME)[1] / Gst.SECOND
            duration = self.pipeline.query_duration(Gst.Format.TIME)[1] / Gst.SECOND
            self.position = position
            self.duration = max(duration, self.track.duration)
            self.emit("position-changed", position, self.duration)
            if self.playing and duration > 0:
                remaining = duration - position
                started_auto_dj = False
                if (self.auto_dj_enabled and self.auto_dj_plan and self.next_track and
                        self.auto_dj_plan.auto_dj and
                        self.auto_dj_plan.next_path == self.next_track.path and
                        remaining <= self.auto_dj_plan.duration):
                    started_auto_dj = self._start_auto_dj_transition()
                if not started_auto_dj and self.crossfade > 0 and remaining <= self.crossfade:
                    if self.auto_dj_enabled and self.next_pipeline:
                        self._stop_pipeline(self.next_pipeline)
                        self.next_pipeline = None
                    self._start_crossfade()
        except Exception:
            pass
        return True

    def _start_crossfade(self):
        if self._crossfading or not self.next_track:
            return
        LOGGER.info(
            "Crossfade starting: %s -> %s (%ss)",
            self.track.title if self.track else "Unknown track",
            self.next_track.title,
            self.crossfade,
        )
        nxt = self._new_pipeline(self.next_track, 0.0)
        if not nxt:
            LOGGER.warning("Crossfade unavailable: could not prepare %s", self.next_track.title)
            return
        self.next_pipeline = nxt
        self._crossfading = True
        nxt.set_state(Gst.State.PLAYING)
        self._fade_step(0)

    def _fade_step(self, step):
        if not self._crossfading or not self.pipeline or not self.next_pipeline:
            return False
        amount = min(1.0, step / 20.0)
        self.pipeline.props.volume = self.volume * math.cos(amount * math.pi / 2)
        self.next_pipeline.props.volume = self.volume * math.sin(amount * math.pi / 2)
        if amount >= 1.0:
            previous_track = self.track
            self._stop_pipeline(self.pipeline)
            self.pipeline, self.track = self.next_pipeline, self.next_track
            self.next_pipeline, self.next_track = None, None
            self._crossfading = False
            self.position = 0.0
            self.duration = self.track.duration
            LOGGER.info(
                "Crossfade completed: now playing %s",
                self.track.title,
            )
            self.emit("track-changed", self.track)
            self.emit("track-transitioned", previous_track, self.track)
            return False
        GLib.timeout_add(100, self._fade_step, step + 1)
        return False

    def _start_auto_dj_transition(self, duration_override=None):
        if self._crossfading or not self.pipeline or not self.next_pipeline or not self.next_track:
            return False
        plan = self.auto_dj_plan
        if plan is None:
            return False
        if plan.next_path != self.next_track.path:
            return False
        if duration_override is not None:
            duration = max(.8, float(duration_override))
        else:
            remaining = max(.8, self.duration - self.position)
            duration = max(.8, min(float(plan.duration), remaining))
        LOGGER.info(
            "Auto DJ transition starting: %s -> %s (%ss, %s)",
            self.track.title if self.track else "Unknown track", self.next_track.title,
            round(duration, 2), plan.mode,
        )
        self._crossfading = True
        self._auto_dj_transition = True
        self._auto_dj_duration = duration
        self._auto_dj_plan = plan
        self.next_pipeline.set_state(Gst.State.PLAYING)
        self.emit("auto-dj-transition-started", plan)
        self._auto_fade_step(0.0)
        return True

    @staticmethod
    def _clamp_volume(value):
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _set_bass(pipeline, gain_db):
        if not pipeline:
            return
        try:
            equalizer = pipeline.props.audio_filter
            if equalizer and equalizer.find_property("band0"):
                equalizer.props.band0 = max(-8.0, min(0.0, float(gain_db)))
        except (AttributeError, TypeError, ValueError):
            pass

    def _auto_fade_step(self, elapsed_ms):
        if not self._auto_dj_transition or not self.pipeline or not self.next_pipeline:
            return False
        plan = self._auto_dj_plan
        amount = min(1.0, elapsed_ms / (self._auto_dj_duration * 1000.0))
        outgoing_gain = getattr(plan, "outgoing_gain", 1.0)
        incoming_gain = getattr(plan, "incoming_gain", 1.0)
        self.pipeline.props.volume = self._clamp_volume(
            self.volume * outgoing_gain * math.cos(amount * math.pi / 2)
        )
        self.next_pipeline.props.volume = self._clamp_volume(
            self.volume * incoming_gain * math.sin(amount * math.pi / 2)
        )
        if getattr(plan, "smart_eq", False):
            self._set_bass(self.pipeline, -6.0 * amount)
            self._set_bass(self.next_pipeline, -6.0 * (1.0 - amount))
        if amount >= 1.0:
            previous_track = self.track
            self._stop_pipeline(self.pipeline)
            self.pipeline, self.track = self.next_pipeline, self.next_track
            self.next_pipeline, self.next_track = None, None
            self._crossfading = False
            self._auto_dj_transition = False
            self.auto_dj_plan = None
            self.position = 0.0
            self.duration = self.track.duration
            self.emit("track-changed", self.track)
            self.emit("track-transitioned", previous_track, self.track)
            self.emit("auto-dj-transition-finished", previous_track, self.track)
            return False
        GLib.timeout_add(60, self._auto_fade_step, elapsed_ms + 60.0)
        return False

    def prepare_next(self, track):
        if self.auto_dj_enabled and self.next_track and self.next_track.path != getattr(track, "path", None):
            self._stop_pipeline(self.next_pipeline)
            self.next_pipeline = None
            self.auto_dj_plan = None
        self.next_track = track
        if track:
            LOGGER.info("Next track prepared for transition: %s", track.title)
            if self.auto_dj_enabled and self.next_pipeline is None:
                self.next_pipeline = self._new_pipeline(track, 0.0, True)
                if self.next_pipeline:
                    self.next_pipeline.set_state(Gst.State.PAUSED)
        else:
            LOGGER.info("No next track prepared; automatic transition disabled")
            self._stop_pipeline(self.next_pipeline)
            self.next_pipeline = None
            self.auto_dj_plan = None

    def set_auto_dj_enabled(self, enabled):
        enabled = bool(enabled)
        if self.auto_dj_enabled == enabled:
            return
        if not enabled:
            if self._auto_dj_transition:
                self._cancel_crossfade("Auto DJ disabled", preserve_next=False)
            else:
                self._stop_pipeline(self.next_pipeline)
                self.next_pipeline = None
                self.next_track = None
        self.auto_dj_enabled = enabled
        self.auto_dj_plan = None

    def set_auto_dj_plan(self, plan):
        if not self.auto_dj_enabled or not self.next_track or not plan:
            self.auto_dj_plan = None
            return
        if plan.next_path == self.next_track.path and plan.current_path == getattr(self.track, "path", None):
            self.auto_dj_plan = plan

    def start_prepared_transition(self, duration=1.8):
        """Use a prepared next stream for an immediate manual Next action."""
        if (not self.auto_dj_enabled or not self.next_pipeline or not self.auto_dj_plan or
                not self.auto_dj_plan.auto_dj):
            return False
        return self._start_auto_dj_transition(duration_override=duration)

    def _cancel_crossfade(self, reason="manual", preserve_next=False):
        if self._crossfading:
            LOGGER.info("Crossfade cancelled (%s)", reason)
        pending_track = self.next_track if preserve_next else None
        if self.next_pipeline:
            self._stop_pipeline(self.next_pipeline)
        self.next_pipeline, self.next_track = None, pending_track
        self._crossfading = False
        self._auto_dj_transition = False
        self._auto_dj_plan = None
        if self.pipeline:
            self.pipeline.props.volume = self.volume

    def _on_message(self, _bus, message, source):
        if message.type == Gst.MessageType.ERROR:
            error, _ = message.parse_error()
            LOGGER.error("GStreamer error: %s", error.message)
            self.emit("error", error.message)
        elif message.type == Gst.MessageType.EOS and source is self.pipeline and not self._crossfading:
            LOGGER.info("Track ended without an active crossfade; handing off to the queue")
            self.emit("finished")

    @staticmethod
    def _stop_pipeline(pipeline):
        if pipeline:
            pipeline.set_state(Gst.State.NULL)

    def close(self):
        self._stop_pipeline(self.pipeline)
        self._stop_pipeline(self.next_pipeline)
