"""Conservative musical transition planning."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .analysis import TrackAnalysis


@dataclass(frozen=True, slots=True)
class TransitionPlan:
    current_path: str
    next_path: str
    duration: float
    mode: str = "gentle"
    outgoing_gain: float = 1.0
    incoming_gain: float = 1.0
    smart_eq: bool = True
    tempo_ratio: float = 1.0
    reason: str = "conservative fallback"
    auto_dj: bool = True


class TransitionPlanner:
    """Choose a safe plan from available evidence, never inventing beats."""

    def plan(self, current, following, left: TrackAnalysis, right: TrackAnalysis, options: dict) -> TransitionPlan:
        style = options.get("style", "balanced")
        if style not in {"subtle", "balanced", "energetic"}:
            style = "balanced"

        description = " ".join(
            str(value or "") for value in (
                getattr(current, "genre", ""), getattr(current, "album", ""),
                getattr(following, "genre", ""), getattr(following, "album", ""),
            )
        ).lower()
        if any(word in description for word in ("podcast", "audiobook", "spoken word")):
            return self._fallback(left, right, "spoken content")
        if any(word in description for word in ("classical", "live", "continuous", "dj mix", "mixtape")):
            return self._fallback(left, right, "gapless/live material")

        durations = {"subtle": (2.0, 4.0), "balanced": (4.0, 8.0), "energetic": (8.0, 12.0)}
        minimum, maximum = durations[style]
        available = min(value for value in (left.duration, right.duration) if value > 0) if left.duration and right.duration else maximum
        duration = min(maximum, max(minimum, available * .22))
        if options.get("silence_detection", True):
            duration = max(2.0, duration - min(left.outro_silence, .8) + min(right.intro_silence, .8))
        requested_length = options.get("length", "automatic")
        if requested_length != "automatic":
            try:
                duration = float(requested_length)
            except (TypeError, ValueError):
                pass
            duration = min(duration, max(2.0, available))

        if left.bpm and right.bpm and left.beat_confidence >= .8 and right.beat_confidence >= .8:
            ratio = right.bpm / left.bpm
            if (options.get("beat_matching", True) and options.get("tempo_matching", True)
                    and .96 <= ratio <= 1.04):
                mode = "beat"
                reason = "confident BPM metadata"
            else:
                ratio = 1.0
                mode = "phrase" if options.get("phrase_matching", True) else "gentle"
                reason = "phrase-safe fallback"
        elif options.get("phrase_matching", True):
            ratio = 1.0
            mode = "phrase"
            reason = "phrase-safe transition without forced beat matching"
        else:
            ratio = 1.0
            mode = "gentle"
            reason = "conservative transition"

        outgoing_gain = self._loudness_gain(left.loudness_lufs)
        incoming_gain = self._loudness_gain(right.loudness_lufs)
        return TransitionPlan(
            current_path=left.path,
            next_path=right.path,
            duration=round(duration, 2),
            mode=mode,
            outgoing_gain=outgoing_gain,
            incoming_gain=incoming_gain,
            smart_eq=bool(options.get("smart_eq", True)),
            tempo_ratio=ratio,
            reason=reason,
        )

    @staticmethod
    def _fallback(left, right, reason):
        return TransitionPlan(
            current_path=left.path, next_path=right.path,
            duration=2.0, mode="fallback", smart_eq=False,
            reason=reason, auto_dj=False,
        )

    @staticmethod
    def _loudness_gain(lufs):
        if lufs is None or not math.isfinite(lufs):
            return 1.0
        # Keep normalization deliberately restrained; it must never clip a
        # track just to make two analyses numerically identical.
        return max(.72, min(1.28, 10 ** ((-14.0 - lufs) / 20.0)))
