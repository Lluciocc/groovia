import logging
import math
import os

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, GObject, Gst

from ..autodj.planner import TransitionPlan

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
        "seeked": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "volume-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "error": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "finished": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "track-transitioned": (GObject.SignalFlags.RUN_FIRST, None, (object, object)),
        "auto-dj-transition-started": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "auto-dj-transition-finished": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (object, object),
        ),
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
        self._tempo_filters = {}
        self._eq_filters = {}
        self._fx_filters = {}
        # A playbin seek is reliable only after PAUSED preroll completes.
        self._pending_incoming_seeks = {}
        self._verified_incoming_seeks = {}
        self.tempo_filter_name, self.tempo_matching_available = self._detect_tempo_filter()
        LOGGER.info(
            "tempo matching filter=%s available=%s",
            self.tempo_filter_name or "none", self.tempo_matching_available,
        )
        GLib.timeout_add(200, self._tick)

    @staticmethod
    def _detect_tempo_filter(factory_find=None):
        Gst.init(None)
        candidates = ("rubberband", "pitch", "scaletempo")
        factory_find = factory_find or Gst.ElementFactory.find
        found = []
        for name in candidates:
            factory = factory_find(name)
            if not factory:
                continue
            plugin = factory.get_plugin()
            found.append((name, plugin.get_name() if plugin else "unknown", plugin.get_filename() if plugin else "unknown"))
        try:
            registry_paths = list(Gst.Registry.get().get_path_list())
        except (AttributeError, TypeError):
            registry_paths = []
        LOGGER.info(
            "GStreamer diagnostics version=%s searched_elements=%s found=%s "
            "GST_PLUGIN_PATH=%s GST_PLUGIN_PATH_1_0=%s GST_PLUGIN_SYSTEM_PATH_1_0=%s "
            "GST_PLUGIN_SCANNER=%s registry_paths=%s",
            Gst.version_string(), list(candidates), found,
            os.environ.get("GST_PLUGIN_PATH"), os.environ.get("GST_PLUGIN_PATH_1_0"),
            os.environ.get("GST_PLUGIN_SYSTEM_PATH_1_0"), os.environ.get("GST_PLUGIN_SCANNER"),
            registry_paths,
        )
        return (found[0][0], True) if found else (None, False)

    def _new_pipeline(self, track, volume, auto_dj=False):
        pipeline = Gst.ElementFactory.make("playbin", None)
        if not pipeline:
            self.emit("error", "GStreamer playback is unavailable on this system.")
            return None
        pipeline.props.uri = (
            track.path
            if track.path.startswith(("http://", "https://"))
            else Gst.filename_to_uri(os.path.abspath(track.path))
        )
        pipeline.props.volume = volume
        if auto_dj:
            audio_filter, tempo_filter, equalizer, effects = self._make_audio_filter()
            if audio_filter:
                pipeline.props.audio_filter = audio_filter
                self._tempo_filters[id(pipeline)] = tempo_filter
                self._eq_filters[id(pipeline)] = equalizer
                self._fx_filters[id(pipeline)] = effects
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_message, pipeline)
        return pipeline

    def _make_audio_filter(self):
        """Build the temporary Auto DJ filter chain, if GStreamer supports it."""
        equalizer = Gst.ElementFactory.make("equalizer-3bands", None)
        tempo_filter = (
            Gst.ElementFactory.make(self.tempo_filter_name, None)
            if self.tempo_filter_name else None
        )
        echo = Gst.ElementFactory.make("audioecho", None)
        reverb = Gst.ElementFactory.make("freeverb", None)
        if echo and echo.find_property("max-delay") and echo.find_property("delay"):
            try:
                echo.props.max_delay = 450 * Gst.MSECOND
                echo.props.delay = 180 * Gst.MSECOND
            except (AttributeError, TypeError, ValueError):
                pass
        for effect in (echo, reverb):
            self._disable_effect(effect)
        elements = [item for item in (tempo_filter, equalizer, echo, reverb) if item]
        if not elements:
            return None, None, None, {}
        if len(elements) == 1:
            return elements[0], tempo_filter, equalizer, {"echo": echo, "reverb": reverb}
        chain = Gst.Bin.new(None)
        for element in elements:
            chain.add(element)
        for first, second in zip(elements, elements[1:]):
            if not first.link(second):
                return None, None, None, {}
        sink_pad = elements[0].get_static_pad("sink")
        source_pad = elements[-1].get_static_pad("src")
        if not sink_pad or not source_pad:
            return None, None, None, {}
        chain.add_pad(Gst.GhostPad.new("sink", sink_pad))
        chain.add_pad(Gst.GhostPad.new("src", source_pad))
        return chain, tempo_filter, equalizer, {"echo": echo, "reverb": reverb}

    @staticmethod
    def _disable_effect(effect):
        if not effect:
            return
        for name, value in (
            ("intensity", 0.0), ("feedback", 0.0), ("wet-level", 0.0),
            ("level", 0.0), ("room-scale", 0.0),
        ):
            try:
                if effect.find_property(name):
                    effect.set_property(name, value)
            except (AttributeError, TypeError, ValueError):
                continue

    def set_track(self, track, autoplay=True):
        if self._crossfading:
            LOGGER.info("Crossfade interrupted by manual track change")
        self._stop_pipeline(self.pipeline)
        self._stop_pipeline(self.next_pipeline)
        self.next_pipeline, self.next_track = None, None
        self.auto_dj_plan = None
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
        if self.auto_dj_enabled and self.next_track and not self.next_pipeline:
            self.next_pipeline = self._new_pipeline(self.next_track, 0.0, True)
            if self.next_pipeline:
                self.next_pipeline.set_state(Gst.State.PAUSED)
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
            self.pipeline.seek_simple(
                Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                max(0, int(seconds * Gst.SECOND)),
            )
            self._cancel_crossfade("seek", preserve_next=True)
            self.emit("seeked", self.position)

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
                auto_dj_plan_matches = bool(
                    self.auto_dj_enabled
                    and self.auto_dj_plan
                    and self.next_track
                    and self.auto_dj_plan.auto_dj
                    and self.auto_dj_plan.current_path == self.track.path
                    and self.auto_dj_plan.next_path == self.next_track.path
                )
                if auto_dj_plan_matches:
                    planned_start = float(getattr(self.auto_dj_plan, "outgoing_start", 0.0) or 0.0)
                    if (
                        not self._crossfading
                        # The planner timestamp is authoritative. Never turn
                        # a semantic exit into an EOS-relative fade because
                        # the plan duration happens to fit in A's tail.
                        and position + 0.02 >= planned_start
                    ):
                        started = self._start_auto_dj_transition()
                        if not started and not self._crossfading:
                            LOGGER.warning(
                                "Auto DJ transition not ready yet: %s -> %s; waiting for EOS fallback",
                                self.track.title if self.track else "Unknown track",
                                (
                                    self.next_track.title
                                    if self.next_track
                                    else "Unknown track"
                                ),
                            )
                    # Do not enter the old crossfade branch while a valid Auto
                    # DJ plan exists. It can discard the preloaded stream and
                    # create the audible gap Auto DJ is meant to avoid.
                    return True
                if self.crossfade > 0 and remaining <= self.crossfade:
                    if self.auto_dj_enabled and self.next_pipeline:
                        self._stop_pipeline(self.next_pipeline)
                        self.next_pipeline = None
                    self._start_crossfade()
        except Exception:
            LOGGER.exception("Audio position tick failed")
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
            LOGGER.warning(
                "Crossfade unavailable: could not prepare %s", self.next_track.title
            )
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
        if self._crossfading or not self.pipeline or not self.next_track:
            return False
        if self.next_pipeline is None:
            LOGGER.warning(
                "Auto DJ next stream was unavailable; recreating it for %s",
                self.next_track.title,
            )
            self.next_pipeline = self._new_pipeline(self.next_track, 0.0, True)
            if self.next_pipeline is None:
                return False
            self.next_pipeline.set_state(Gst.State.PAUSED)
        plan = self.auto_dj_plan
        if plan is None:
            return False
        if not getattr(plan, "auto_dj", True):
            return False
        if plan.next_path != self.next_track.path:
            return False
        if not self._incoming_seek_ready(plan):
            LOGGER.debug(
                "AutoDJ waiting for incoming preroll before transition B@%.3f",
                float(getattr(plan, "incoming_start", 0.0) or 0.0),
            )
            return False
        if duration_override is not None:
            duration = max(0.8, float(duration_override))
        else:
            planned_start = float(getattr(plan, "outgoing_start", 0.0) or 0.0)
            elapsed_since_plan_start = max(0.0, self.position - planned_start)
            # Account only for a delayed main-loop tick after the authoritative
            # timestamp; do not derive the transition start from track EOS.
            duration = max(0.8, float(plan.duration) - elapsed_since_plan_start)
        LOGGER.info(
            "Auto DJ transition starting: %s -> %s (%ss, %s)",
            self.track.title if self.track else "Unknown track",
            self.next_track.title,
            round(duration, 2),
            getattr(plan, "strategy", plan.mode),
        )
        LOGGER.info(
            "AutoDJ transition started %r -> %r mode=%s duration=%.3fs "
            "outgoing_start=%.3f incoming_start=%.3f confidence=%.2f",
            self.track.title if self.track else "Unknown track", self.next_track.title,
            getattr(plan, "strategy", plan.mode), duration, plan.outgoing_start, plan.incoming_start, plan.confidence,
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

    def _set_bass(self, pipeline, gain_db):
        if not pipeline:
            return
        try:
            equalizer = self._eq_filters.get(id(pipeline))
            if equalizer and equalizer.find_property("band0"):
                equalizer.props.band0 = max(-8.0, min(0.0, float(gain_db)))
        except (AttributeError, TypeError, ValueError):
            pass

    def _set_treble(self, pipeline, gain_db):
        if not pipeline:
            return
        try:
            equalizer = self._eq_filters.get(id(pipeline))
            if equalizer and equalizer.find_property("band2"):
                equalizer.props.band2 = max(-8.0, min(0.0, float(gain_db)))
        except (AttributeError, TypeError, ValueError):
            pass

    @staticmethod
    def _set_effect_level(effect, amount):
        if not effect:
            return
        amount = max(0.0, min(1.0, float(amount)))
        for name, value in (
            ("intensity", amount), ("wet-level", amount),
            ("level", amount), ("room-scale", amount),
        ):
            try:
                if effect.find_property(name):
                    effect.set_property(name, value)
            except (AttributeError, TypeError, ValueError):
                continue
        try:
            if effect.find_property("feedback"):
                effect.set_property("feedback", min(0.28, amount * 0.28))
            if effect.find_property("delay"):
                effect.set_property("delay", 180 * Gst.MSECOND)
        except (AttributeError, TypeError, ValueError):
            pass

    def _apply_transition_fx(self, plan, amount):
        strategy = getattr(plan, "strategy", "clean_blend")
        outgoing_effects = self._fx_filters.get(id(self.pipeline), {})
        incoming_effects = self._fx_filters.get(id(self.next_pipeline), {})
        if strategy in ("echo_out", "beat_repeat_out"):
            self._set_effect_level(outgoing_effects.get("echo"), amount * 0.45)
        elif strategy == "reverb_out":
            self._set_effect_level(outgoing_effects.get("reverb"), amount * 0.30)
        elif strategy == "filter_out":
            self._set_effect_level(outgoing_effects.get("echo"), amount * 0.12)
            self._set_treble(self.pipeline, -4.0 * amount)
        self._set_effect_level(incoming_effects.get("echo"), 0.0)
        self._set_effect_level(incoming_effects.get("reverb"), 0.0)

    def _auto_fade_step(self, elapsed_ms):
        if not self._auto_dj_transition or not self.pipeline or not self.next_pipeline:
            return False
        plan = self._auto_dj_plan
        amount = min(1.0, elapsed_ms / (self._auto_dj_duration * 1000.0))
        outgoing_gain = getattr(plan, "outgoing_gain", 1.0)
        incoming_gain = getattr(plan, "incoming_gain", 1.0)
        strategy = getattr(plan, "strategy", "clean_blend")
        if strategy == "hard_cut":
            outgoing_curve = 1.0 if amount < 0.62 else max(0.0, 1.0 - (amount - 0.62) / 0.18)
            incoming_curve = 0.0 if amount < 0.62 else min(1.0, (amount - 0.62) / 0.18)
        else:
            outgoing_curve = math.cos(amount * math.pi / 2)
            incoming_curve = math.sin(amount * math.pi / 2)
        self.pipeline.props.volume = self._clamp_volume(
            self.volume * outgoing_gain * outgoing_curve
        )
        self.next_pipeline.props.volume = self._clamp_volume(
            self.volume * incoming_gain * incoming_curve
        )
        if getattr(plan, "smart_eq", False) and strategy in ("clean_blend", "bass_swap", "vocal_handoff", "filter_out"):
            strength = max(0.0, min(1.0, getattr(plan, "eq_strength", 0.55)))
            self._set_bass(self.pipeline, -8.0 * strength * amount)
            self._set_bass(self.next_pipeline, -8.0 * strength * (1.0 - amount))
        self._apply_transition_fx(plan, amount)
        if amount >= 1.0:
            previous_track = self.track
            self._stop_pipeline(self.pipeline)
            self.pipeline, self.track = self.next_pipeline, self.next_track
            self.next_pipeline, self.next_track = None, None
            self._crossfading = False
            self._auto_dj_transition = False
            self.auto_dj_plan = None
            self._reset_dsp(self.pipeline)
            self.position = 0.0
            self.duration = self.track.duration
            LOGGER.info(
                "AutoDJ transition finished %r -> %r",
                previous_track.title if previous_track else "Unknown track", self.track.title,
            )
            self.emit("track-changed", self.track)
            self.emit("track-transitioned", previous_track, self.track)
            self.emit("auto-dj-transition-finished", previous_track, self.track)
            return False
        GLib.timeout_add(60, self._auto_fade_step, elapsed_ms + 60.0)
        return False

    def prepare_next(self, track):
        requested_path = getattr(track, "path", None) if track else None
        current_next_path = getattr(self.next_track, "path", None) if self.next_track else None
        same_next_track = bool(track and self.next_track and requested_path == current_next_path)

        if same_next_track:
            # Queue updates can repeat while analysis and preroll are in
            # flight. Preserve both the pipeline and the real plan; in
            # particular, never reinstall the temporary analysis fallback.
            LOGGER.debug(
                "AutoDJ duplicate prepare_next ignored current=%s next=%s plan=%s",
                getattr(self.track, "path", None), requested_path,
                "present" if self.auto_dj_plan else "missing",
            )
            recreated_pipeline = False
            if self.auto_dj_enabled and self.next_pipeline is None:
                self.next_pipeline = self._new_pipeline(track, 0.0, True)
                if self.next_pipeline:
                    self.next_pipeline.set_state(Gst.State.PAUSED)
                    recreated_pipeline = True
            if recreated_pipeline and self.auto_dj_plan is not None:
                self._configure_next_pipeline(self.auto_dj_plan)
            else:
                self._install_fallback_plan_if_missing(track)
            return

        if track is None and self.next_track is None and self.next_pipeline is None and self.auto_dj_plan is None:
            LOGGER.debug("AutoDJ duplicate prepare_next ignored next=None")
            return

        if self.next_track is not None or self.next_pipeline is not None or self.auto_dj_plan is not None:
            # The queued path really changed (including B -> None), so the
            # old preroll and its plan are no longer associated with it.
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
            self._install_fallback_plan_if_missing(track)
        else:
            LOGGER.info("No next track prepared; automatic transition disabled")

    def _install_fallback_plan_if_missing(self, track):
        """Install the temporary safety plan without replacing analysis."""
        if (
            not self.auto_dj_enabled
            or not track
            or not self.track
            or track.path == self.track.path
            or self.next_pipeline is None
            or self.auto_dj_plan is not None
        ):
            return
        # Analysis runs asynchronously. Keep an immediate, short Auto DJ
        # plan so playback has a safe transition while analysis is pending.
        fallback_duration = min(2.0, max(0.8, float(self.duration or track.duration or 2.0)))
        fallback_end = float(self.duration or self.track.duration or fallback_duration)
        self.auto_dj_plan = TransitionPlan(
            current_path=self.track.path,
            next_path=track.path,
            duration=fallback_duration,
            mode="fallback",
            outgoing_start=max(0.0, fallback_end - fallback_duration),
            outgoing_end=fallback_end,
            smart_eq=False,
            reason="analysis pending",
        )
        self._configure_next_pipeline(self.auto_dj_plan)

    def set_auto_dj_enabled(self, enabled):
        enabled = bool(enabled)
        if self.auto_dj_enabled == enabled:
            return
        if not enabled:
            if self._auto_dj_transition:
                self._cancel_crossfade("Auto DJ disabled", preserve_next=False)
            else:
                self._reset_dsp(self.pipeline)
                self._stop_pipeline(self.next_pipeline)
                self.next_pipeline = None
                self.next_track = None
        self.auto_dj_enabled = enabled
        self.auto_dj_plan = None

    def set_auto_dj_plan(self, plan):
        if not self.auto_dj_enabled or not self.next_track or not plan:
            self.auto_dj_plan = None
            return
        if plan.next_path == self.next_track.path and plan.current_path == getattr(
            self.track, "path", None
        ):
            existing = self.auto_dj_plan
            if existing == plan:
                LOGGER.debug(
                    "AutoDJ duplicate plan ignored current=%s next=%s incoming_start=%.3f",
                    plan.current_path, plan.next_path, plan.incoming_start,
                )
                return
            if (
                existing is not None
                and existing.current_path == plan.current_path
                and existing.next_path == plan.next_path
                and getattr(existing, "mode", "fallback") != "fallback"
                and getattr(plan, "mode", "fallback") == "fallback"
            ):
                LOGGER.debug(
                    "AutoDJ fallback plan ignored; analysed plan already exists current=%s next=%s",
                    plan.current_path, plan.next_path,
                )
                return
            self.auto_dj_plan = plan
            self._configure_next_pipeline(plan)
            LOGGER.info(
                "Auto DJ plan ready: %s -> %s (%ss, %s)",
                self.track.title if self.track else "Unknown track",
                self.next_track.title,
                round(plan.duration, 2),
                plan.reason,
            )

    def start_prepared_transition(self, duration=1.8):
        """Use a prepared next stream for an immediate manual Next action."""
        if (
            not self.auto_dj_enabled
            or not self.next_pipeline
            or not self.auto_dj_plan
            or not self.auto_dj_plan.auto_dj
        ):
            return False
        return self._start_auto_dj_transition(duration_override=duration)

    def _cancel_crossfade(self, reason="manual", preserve_next=False):
        if self._crossfading:
            LOGGER.info("Crossfade cancelled (%s)", reason)
            if self._auto_dj_transition:
                LOGGER.info("AutoDJ transition cancelled reason=%s", reason)
        pending_track = self.next_track if preserve_next else None
        if self.next_pipeline:
            self._stop_pipeline(self.next_pipeline)
        self.next_pipeline, self.next_track = None, pending_track
        self._crossfading = False
        self._auto_dj_transition = False
        self._auto_dj_plan = None
        if self.pipeline:
            self.pipeline.props.volume = self.volume
            self._reset_dsp(self.pipeline)
        if self.next_pipeline:
            self._reset_dsp(self.next_pipeline)

    def _configure_next_pipeline(self, plan):
        """Apply DSP and queue B's seek until PAUSED preroll is complete."""
        if not self.next_pipeline:
            return
        effects = self._fx_filters.get(id(self.next_pipeline), {})
        available_effects = [name for name, effect in effects.items() if effect]
        LOGGER.info(
            "AutoDJ DSP configured strategy=%s tempo_filter=%s effects=%s",
            getattr(plan, "strategy", "fallback"), self.tempo_filter_name or "none", available_effects,
        )
        strategy = getattr(plan, "strategy", "clean_blend")
        if strategy in ("echo_out", "beat_repeat_out") and not effects.get("echo"):
            LOGGER.info("AutoDJ effect fallback strategy=%s -> clean_blend (audioecho unavailable)", strategy)
        if strategy == "reverb_out" and not effects.get("reverb"):
            LOGGER.info("AutoDJ effect fallback strategy=reverb_out -> clean_blend (freeverb unavailable)")
        tempo = self._tempo_filters.get(id(self.next_pipeline))
        ratio = float(getattr(plan, "tempo_ratio", 1.0) or 1.0)
        if tempo and abs(ratio - 1.0) > 0.0001:
            try:
                if tempo.find_property("tempo"):
                    tempo.props.tempo = ratio
                elif tempo.find_property("rate"):
                    tempo.props.rate = ratio
                LOGGER.info("AutoDJ tempo applied filter=%s ratio=%.5f", self.tempo_filter_name, ratio)
            except (AttributeError, TypeError, ValueError):
                LOGGER.warning("AutoDJ tempo filter rejected ratio %.5f", ratio)
        start = max(0.0, float(getattr(plan, "incoming_start", 0.0) or 0.0))
        pipeline_key = id(self.next_pipeline)
        self._pending_incoming_seeks[pipeline_key] = start
        self._verified_incoming_seeks.pop(pipeline_key, None)
        self._apply_pending_incoming_seek(self.next_pipeline)

    def _incoming_seek_ready(self, plan):
        """Ensure B is positioned at the plan timestamp before PLAYING."""
        if not self.next_pipeline:
            return False
        pipeline_key = id(self.next_pipeline)
        start = max(0.0, float(getattr(plan, "incoming_start", 0.0) or 0.0))
        if self._verified_incoming_seeks.get(pipeline_key) == start:
            return True
        if pipeline_key not in self._pending_incoming_seeks:
            self._configure_next_pipeline(plan)
        self._apply_pending_incoming_seek(self.next_pipeline)
        return self._verified_incoming_seeks.get(pipeline_key) == start

    def _apply_pending_incoming_seek(self, pipeline):
        """Seek a preroll-complete playbin and record that it was verified."""
        if not pipeline:
            return False
        pipeline_key = id(pipeline)
        if pipeline_key not in self._pending_incoming_seeks:
            return pipeline_key in self._verified_incoming_seeks
        try:
            state_result, current_state, pending_state = pipeline.get_state(0)
        except (AttributeError, TypeError, ValueError):
            LOGGER.debug("AutoDJ cannot verify preroll for incoming pipeline")
            return False
        if state_result != Gst.StateChangeReturn.SUCCESS or current_state != Gst.State.PAUSED:
            LOGGER.debug(
                "AutoDJ incoming seek deferred until PAUSED preroll result=%s state=%s pending=%s",
                state_result, getattr(current_state, "value_nick", current_state),
                getattr(pending_state, "value_nick", pending_state),
            )
            return False
        start = self._pending_incoming_seeks[pipeline_key]
        if start <= 0.0:
            success = True
        else:
            success = bool(pipeline.seek_simple(
                Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                int(start * Gst.SECOND),
            ))
        if not success:
            LOGGER.warning("AutoDJ incoming seek failed after preroll start=%.3f", start)
            return False
        self._pending_incoming_seeks.pop(pipeline_key, None)
        self._verified_incoming_seeks[pipeline_key] = start
        LOGGER.info("AutoDJ incoming seek verified after preroll B@%.3f", start)
        return True

    def _reset_dsp(self, pipeline):
        """Restore temporary Auto DJ volume/EQ/tempo controls."""
        if not pipeline:
            return
        try:
            pipeline.props.volume = self.volume
        except (AttributeError, TypeError):
            pass
        tempo = self._tempo_filters.get(id(pipeline))
        if tempo:
            for name in ("tempo", "rate", "pitch"):
                try:
                    if tempo.find_property(name):
                        setattr(tempo.props, name, 1.0)
                except (AttributeError, TypeError, ValueError):
                    continue
        try:
            equalizer = self._eq_filters.get(id(pipeline))
            if equalizer and equalizer.find_property("band0"):
                equalizer.props.band0 = 0.0
            if equalizer and equalizer.find_property("band2"):
                equalizer.props.band2 = 0.0
        except (AttributeError, TypeError, ValueError):
            pass
        for effect in self._fx_filters.get(id(pipeline), {}).values():
            self._disable_effect(effect)

    def _on_message(self, _bus, message, source):
        if message.type == Gst.MessageType.ERROR:
            error, _ = message.parse_error()
            LOGGER.error("GStreamer error: %s", error.message)
            self.emit("error", error.message)
        elif message.type == Gst.MessageType.ASYNC_DONE and source is self.next_pipeline:
            # playbin has completed its PAUSED preroll; only now is the
            # incoming timestamp seek considered valid for execution.
            self._apply_pending_incoming_seek(source)
        elif (
            message.type == Gst.MessageType.EOS
            and source is self.pipeline
            and not self._crossfading
        ):
            if self.auto_dj_enabled and self.next_pipeline and self.next_track:
                if not self.auto_dj_plan:
                    self.auto_dj_plan = TransitionPlan(
                        current_path=self.track.path,
                        next_path=self.next_track.path,
                        duration=0.8,
                        mode="fallback",
                        outgoing_start=max(0.0, self.position - 0.8),
                        outgoing_end=max(self.position, 0.0),
                        smart_eq=False,
                        reason="end-of-stream fallback",
                    )
                if self._start_auto_dj_transition(duration_override=0.8):
                    return
            LOGGER.info(
                "Track ended without an active crossfade; handing off to the queue"
            )
            self.emit("finished")

    def _stop_pipeline(self, pipeline):
        if pipeline:
            pipeline.set_state(Gst.State.NULL)
            self._tempo_filters.pop(id(pipeline), None)
            self._eq_filters.pop(id(pipeline), None)
            self._fx_filters.pop(id(pipeline), None)
            self._pending_incoming_seeks.pop(id(pipeline), None)
            self._verified_incoming_seeks.pop(id(pipeline), None)

    def close(self):
        self._stop_pipeline(self.pipeline)
        self._stop_pipeline(self.next_pipeline)
