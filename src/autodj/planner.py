"""Explainable, candidate-based musical transition planning."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from .analysis import TrackAnalysis

LOGGER = logging.getLogger("groovia.autodj")


@dataclass(frozen=True, slots=True)
class TransitionPlan:
    current_path: str
    next_path: str
    duration: float
    mode: str = "gentle"
    outgoing_start: float = 0.0
    outgoing_end: float = 0.0
    incoming_start: float = 0.0
    incoming_end: float = 0.0
    tempo_ratio: float = 1.0
    beat_offset: float = 0.0
    downbeat_offset: float = 0.0
    beats_used: int = 0
    bars_used: int = 0
    outgoing_gain: float = 1.0
    incoming_gain: float = 1.0
    smart_eq: bool = True
    eq_strength: float = 0.55
    confidence: float = 0.0
    reason: str = "conservative fallback"
    auto_dj: bool = True


class TransitionPlanner:
    """Choose a safe plan from several musically meaningful candidates."""

    STYLE_BARS = {"subtle": (1, 2), "balanced": (2, 4), "energetic": (4, 8)}
    SCORE_WEIGHTS = {
        "rhythm": 0.20,
        "phrase_alignment_score": 0.16,
        "tempo": 0.10,
        "energy": 0.10,
        "harmonic": 0.08,
        "vocal_score": 0.08,
        "silence": 0.03,
        "structure": 0.05,
        "vocal_handoff_score": 0.20,
        "intro_skip_penalty": -0.10,
        # Vocal-over-vocal is the strongest negative signal. A clean handoff
        # still wins, but a dense overlap must not be rescued by rhythm alone.
        "vocal_overlap_penalty": -0.25,
    }

    def plan(self, current, following, left: TrackAnalysis, right: TrackAnalysis, options: dict) -> TransitionPlan:
        style = options.get("style", "balanced")
        if style not in self.STYLE_BARS:
            style = "balanced"
        description = " ".join(str(value or "") for value in (
            getattr(current, "genre", ""), getattr(current, "album", ""),
            getattr(following, "genre", ""), getattr(following, "album", ""),
        )).lower()
        if any(word in description for word in ("podcast", "audiobook", "spoken word")):
            return self._fallback(left, right, "spoken content: beat matching and bass swap disabled")
        if any(word in description for word in ("classical", "live", "continuous", "dj mix", "mixtape")):
            return self._fallback(left, right, "gapless/live material: preserving the recording")

        tempo_factor, tempo_reason, beat_cap = self._tempo_policy(left, right, options)
        if tempo_reason:
            LOGGER.info("AutoDJ tempo decision %s -> %s: %s", left.path, right.path, tempo_reason)
        LOGGER.info(
            "AutoDJ pair diagnostics bpm_a=%.2f source_a=%s confidence_a=%.2f stability_a=%.2f "
            "beats_a=%d downbeats_a=%d phrases_a=%d bpm_b=%.2f source_b=%s confidence_b=%.2f "
            "stability_b=%.2f beats_b=%d downbeats_b=%d phrases_b=%d key_a=%s key_b=%s "
            "key_confidence=%.2f/%.2f energy=%.2f/%.2f vocal_density=%.2f/%.2f lyrics=%s/%s "
            "vocal_entries=%d/%d vocal_exits=%d/%d tempo_ratio=%.4f stretch=%.2f candidates_pending",
            left.bpm or 0.0, left.source_bpm, left.beat_confidence, left.tempo_stability, len(left.beats),
            len(left.downbeats), len(left.phrase_boundaries), right.bpm or 0.0, right.source_bpm,
            right.beat_confidence, right.tempo_stability, len(right.beats), len(right.downbeats),
            len(right.phrase_boundaries), left.key or "unknown", right.key or "unknown",
            left.key_confidence, right.key_confidence, left.energy or 0, right.energy or 0,
            left.vocal_density or 0, right.vocal_density or 0, left.lyrics_source or "none",
            right.lyrics_source or "none", len(self._vocal_entries(left)), len(self._vocal_entries(right)),
            len(self._vocal_exits(left)), len(self._vocal_exits(right)), tempo_factor,
            abs(tempo_factor - 1),
        )
        candidates = self._candidates(left, right, style, options, tempo_factor, beat_cap)
        LOGGER.info("AutoDJ generated %d candidates for %s -> %s", len(candidates), left.path, right.path)
        if not candidates:
            return self._fallback(left, right, tempo_reason or "no compatible musical candidate")

        scored = []
        for candidate in candidates:
            scores = self._score_candidate(candidate, left, right, tempo_factor)
            total = sum(self.SCORE_WEIGHTS[name] * scores[name] for name in self.SCORE_WEIGHTS)
            confidence = self._confidence(total, left, right, candidate)
            candidate["scores"] = scores
            candidate["total"] = total
            candidate["confidence"] = confidence
            LOGGER.info(
                "AutoDJ candidate %s incoming_start=%.2f score=%.2f outgoing_start=%.2f duration=%.2f "
                "rhythm=%.2f vocal_handoff=%.2f intro_skip_penalty=%.2f "
                "vocal_overlap_penalty=%.2f phrase_alignment=%.2f reason=%r",
                candidate["strategy"], candidate["incoming_start"], total, candidate["outgoing_start"],
                candidate["duration"], scores["rhythm"],
                scores["vocal_handoff_score"], scores["intro_skip_penalty"],
                scores["vocal_overlap_penalty"], scores["phrase_alignment_score"],
                self._candidate_reason(candidate, scores),
            )
            scored.append(candidate)
        # Equal musical scores should keep the useful intro rather than
        # arbitrarily skipping deep into the incoming recording.
        best = max(scored, key=lambda item: (item["confidence"], item["total"], -item["incoming_start"]))
        if best["confidence"] < 0.38:
            return self._fallback(left, right, "low confidence after rhythm/phrase/vocal scoring")

        scores = best["scores"]
        reason_parts = []
        if best["mode"] == "beat_tempo":
            reason_parts.append(f"tempo matched {tempo_factor:.3f}x with pitch preservation")
        elif best["mode"] == "beat":
            reason_parts.append("confident beat alignment")
        else:
            reason_parts.append("beat matching not forced")
        phrase_quality = scores["phrase_alignment_score"]
        vocal_quality = 1.0 - scores["vocal_overlap_penalty"]
        harmonic_quality = scores["harmonic"]
        if best["mode"] == "vocal_handoff":
            reason_parts = ["clean outgoing vocal end", "incoming vocal phrase starts on structural boundary"]
        elif best["incoming_start"] > 2.0 and self._vocal_entries(right):
            reason_parts.append("intro skipped to a stronger vocal entry")
        elif best["incoming_start"] <= 1.0:
            reason_parts.append("instrumental blend retained")
        if phrase_quality >= 0.7:
            reason_parts.append("aligned phrase boundaries")
        if vocal_quality >= 0.75:
            reason_parts.append("low vocal overlap")
        elif vocal_quality < 0.45:
            reason_parts.append("vocal overlap accepted for stronger rhythm")
        if harmonic_quality >= 0.75:
            reason_parts.append("harmonically compatible")
        elif harmonic_quality < 0.45:
            reason_parts.append("key clash softened by conservative blend")
        if tempo_reason and tempo_reason != "beats compatible":
            reason_parts.insert(0, tempo_reason)
        reason = "; ".join(reason_parts)
        plan = TransitionPlan(
            current_path=left.path,
            next_path=right.path,
            duration=round(best["duration"], 3),
            mode=best["mode"],
            outgoing_start=round(best["outgoing_start"], 3),
            outgoing_end=round(best["outgoing_end"], 3),
            incoming_start=round(best["incoming_start"], 3),
            incoming_end=round(best["incoming_end"], 3),
            tempo_ratio=round(tempo_factor if best["mode"] == "beat_tempo" else 1.0, 5),
            beat_offset=round(best["beat_offset"], 4),
            downbeat_offset=round(best["downbeat_offset"], 4),
            beats_used=best["beats_used"],
            bars_used=best["bars_used"],
            outgoing_gain=self._loudness_gain(left.loudness_lufs, left.peak_db),
            incoming_gain=self._loudness_gain(right.loudness_lufs, right.peak_db),
            smart_eq=bool(options.get("smart_eq", True)),
            eq_strength=round(self._eq_strength(left, right, style), 3),
            confidence=round(best["confidence"], 3),
            reason=reason,
        )
        LOGGER.info(
            'AutoDJ selected %s A@%.2f -> B@%.2f duration=%.3f bars=%d beats=%d confidence=%.2f reason=%r',
            plan.mode, plan.outgoing_start, plan.incoming_start, plan.duration, plan.bars_used,
            plan.beats_used, plan.confidence, plan.reason,
        )
        return plan

    def _tempo_policy(self, left, right, options):
        if not left.bpm or not right.bpm:
            if options.get("beat_matching", True):
                LOGGER.info("beatmatch rejected: missing BPM audio evidence")
            return 1.0, "BPM unavailable; phrase-aware candidate only", False
        ratio = left.bpm / right.bpm
        delta = abs(ratio - 1.0)
        confidence = min(left.beat_confidence, right.beat_confidence)
        if confidence < 0.52 or not left.beats or not right.beats:
            LOGGER.info("beatmatch rejected: beat confidence %.2f or timeline missing", confidence)
            return ratio, "beat confidence/timeline insufficient", False
        if not options.get("beat_matching", True):
            return ratio, "beat matching disabled by settings", False
        # The policy is ±4% whenever tempo matching is enabled.  Availability
        # of the stretch backend decides whether that range can be used, not
        # whether the ratio is considered musically compatible.
        max_delta = 0.04 if options.get("tempo_matching", True) else 0.01
        if delta > max_delta:
            LOGGER.info("beatmatch rejected: tempo ratio %.3f exceeds limit %.3f", ratio, max_delta)
            return ratio, f"tempo incompatible ({ratio:.3f}x exceeds {max_delta:.2f})", False
        if delta > 0.01 and not options.get("tempo_matching_available", False):
            LOGGER.info("beatmatch rejected: tempo stretch plugin unavailable")
            return ratio, "tempo stretch unavailable; phrase blend", False
        return ratio, "beats compatible" if delta <= 0.01 else "slight tempo stretch available", True

    def _candidates(self, left, right, style, options, tempo_factor, beat_cap):
        available = min(value for value in (left.duration, right.duration) if value > 0) if left.duration and right.duration else 60.0
        custom = options.get("length", "automatic")
        if custom != "automatic":
            try:
                lengths = [max(1.5, min(float(custom), available))]
            except (TypeError, ValueError):
                lengths = []
        elif beat_cap and left.bpm:
            lengths = [bars * 4 * 60.0 / left.bpm for bars in self.STYLE_BARS[style]]
        else:
            minimum, maximum = {"subtle": (2.0, 4.5), "balanced": (3.0, 9.5), "energetic": (6.0, 14.0)}[style]
            lengths = [min(maximum, max(minimum, available * fraction)) for fraction in (0.16, 0.22, 0.30)]
        incoming_points = self._semantic_incoming_points(right)
        vocal_exits = [
            value for value in self._vocal_exits(left)
            if max(1.5, 0.0) <= left.duration - value <= 14.0
        ]
        vocal_exits = self._select_semantic_values(vocal_exits, minimum_spacing=1.5, maximum=4)
        result = []
        for duration in lengths:
            if duration > available:
                continue
            bars = round(duration * (left.bpm or 0) / 60 / 4) if left.bpm and beat_cap else 0
            beats = max(0, round(duration * (left.bpm or 0) / 60)) if left.bpm and beat_cap else 0
            outgoing_start = max(0.0, (left.duration or duration) - duration)
            for incoming_start, incoming_kind in incoming_points:
                if right.duration and incoming_start + duration > right.duration - 0.05:
                    continue
                mode = "beat_tempo" if beat_cap and abs(tempo_factor - 1) > 0.01 else ("beat" if beat_cap else ("phrase" if options.get("phrase_matching", True) else "gentle"))
                result.append({
                    "duration": duration, "outgoing_start": outgoing_start,
                    "outgoing_end": left.duration or duration, "incoming_start": incoming_start,
                    "incoming_end": incoming_start + duration, "bars_used": bars,
                    "beats_used": beats, "mode": mode,
                    "incoming_kind": incoming_kind, "outgoing_kind": "rhythmic",
                    "strategy": "instrumental_blend" if incoming_kind in ("intro_start", "instrumental_blend") else incoming_kind,
                    "beat_offset": self._phase_offset(left.beats, outgoing_start, right.beats, incoming_start),
                    "downbeat_offset": self._phase_offset(left.downbeats, outgoing_start, right.downbeats, incoming_start),
                })
            # A lyric/audio vocal exit is a separate family of candidates. Its
            # duration is allowed to be the actual tail length, rather than
            # forcing the tail into the style's preferred bar count.
            for vocal_exit in vocal_exits:
                vocal_duration = left.duration - vocal_exit
                if not 1.5 <= vocal_duration <= min(14.0, available):
                    continue
                vocal_bars = round(vocal_duration * (left.bpm or 0) / 60 / 4) if left.bpm and beat_cap else 0
                vocal_beats = round(vocal_duration * (left.bpm or 0) / 60) if left.bpm and beat_cap else 0
                for incoming_start, incoming_kind in incoming_points:
                    if incoming_kind not in ("vocal_entry", "strong_vocal_entry"):
                        continue
                    if right.duration and incoming_start + vocal_duration > right.duration - 0.05:
                        continue
                    result.append({
                        "duration": vocal_duration, "outgoing_start": vocal_exit,
                        "outgoing_end": left.duration or vocal_duration,
                        "incoming_start": incoming_start,
                        "incoming_end": incoming_start + vocal_duration,
                        "bars_used": vocal_bars, "beats_used": vocal_beats,
                        "mode": "vocal_handoff", "incoming_kind": incoming_kind,
                        "outgoing_kind": "vocal_exit",
                        "strategy": "vocal_handoff",
                        "beat_offset": self._phase_offset(left.beats, vocal_exit, right.beats, incoming_start),
                        "downbeat_offset": self._phase_offset(left.downbeats, vocal_exit, right.downbeats, incoming_start),
                    })
        return result

    @staticmethod
    def _score_candidate(candidate, left, right, tempo_factor):
        phrase = min(1.0, 0.5 * TransitionPlanner._boundary_score(left.phrase_boundaries, candidate["outgoing_start"]) + 0.5 * TransitionPlanner._boundary_score(right.phrase_boundaries, candidate["incoming_start"])) if left.phrase_boundaries or right.phrase_boundaries else 0.32
        rhythm = min(left.beat_confidence, right.beat_confidence) if candidate["mode"].startswith("beat") else 0.42
        if left.beats and right.beats:
            rhythm = min(1.0, rhythm * 0.55 + (1 - min(1.0, abs(candidate["beat_offset"]) / 0.18)) * 0.45)
        energy = 1.0 - min(1.0, abs(TransitionPlanner._curve_at(left.energy_curve, 0.94) - TransitionPlanner._curve_at(right.energy_curve, 0.10)))
        harmonic = TransitionPlanner._harmonic_score(left.key, right.key)
        overlap = TransitionPlanner._vocal_overlap_fraction(left, right, candidate["outgoing_start"], candidate["incoming_start"], candidate["duration"])
        vocal = 1.0 - overlap
        outgoing_exit = TransitionPlanner._point_score(
            candidate["outgoing_start"], TransitionPlanner._vocal_exits(left), 0.55
        )
        incoming_entry = TransitionPlanner._point_score(
            candidate["incoming_start"], TransitionPlanner._vocal_entries(right), 0.55
        )
        phrase_alignment = min(1.0, 0.55 * TransitionPlanner._boundary_score(left.phrase_boundaries, candidate["outgoing_start"]) + 0.45 * TransitionPlanner._boundary_score(right.phrase_boundaries, candidate["incoming_start"]))
        is_vocal_entry = candidate["incoming_kind"] in ("vocal_entry", "strong_vocal_entry")
        if candidate["mode"] == "vocal_handoff":
            vocal_handoff = (
                0.28 * outgoing_exit
                + 0.28 * incoming_entry
                + 0.20 * vocal
                + 0.14 * phrase_alignment
                + 0.10 * energy
            )
        elif is_vocal_entry:
            # A normal beat/phrase candidate landing on a vocal entry is still
            # a vocal handoff opportunity.  Keep its score explicit in logs;
            # the dedicated handoff family gets the additional outgoing-exit
            # evidence and therefore remains stronger when it is clean.
            vocal_handoff = (
                0.34 * incoming_entry
                + 0.26 * vocal
                + 0.24 * phrase_alignment
                + 0.16 * energy
            )
        else:
            vocal_handoff = 0.0
        intro_skip = TransitionPlanner._intro_skip_penalty(right, candidate["incoming_start"])
        silence = min(1.0, 0.5 + min(left.outro_silence, 1.0) * 0.25 + min(right.intro_silence, 1.0) * 0.25)
        structure = phrase * 0.7 + silence * 0.3
        tempo = max(0.0, 1.0 - abs(tempo_factor - 1.0) / 0.08) if left.bpm and right.bpm else 0.35
        return {
            "rhythm": rhythm,
            "phrase_alignment_score": phrase_alignment,
            "tempo": tempo,
            "energy": energy,
            "harmonic": harmonic,
            "vocal_score": vocal,
            "silence": silence,
            "structure": structure,
            "vocal_handoff_score": vocal_handoff,
            "intro_skip_penalty": intro_skip,
            "vocal_overlap_penalty": overlap,
        }

    @staticmethod
    def _confidence(total, left, right, candidate):
        evidence = 0.35
        evidence += 0.30 * min(left.beat_confidence, right.beat_confidence)
        evidence += 0.20 * min(left.tempo_stability, right.tempo_stability)
        evidence += 0.10 * min(left.key_confidence or 0, right.key_confidence or 0)
        evidence += 0.05 if TransitionPlanner._vocal_sections(left) or TransitionPlanner._vocal_sections(right) else 0
        return max(0.0, min(1.0, total + evidence * 0.22))

    @staticmethod
    def _boundary_score(boundaries, point):
        if not boundaries:
            return 0.35
        distance = min(abs(value - point) for value in boundaries)
        return max(0.0, 1.0 - distance / 1.2)

    @staticmethod
    def _phase_offset(first, first_point, second, second_point):
        if not first or not second:
            return 0.0
        a = min(first, key=lambda value: abs(value - first_point))
        b = min(second, key=lambda value: abs(value - second_point))
        return b - second_point - (a - first_point)

    @staticmethod
    def _curve_at(curve, position):
        if not curve:
            return 0.5
        return float(curve[min(len(curve) - 1, max(0, int(position * len(curve))))])

    @staticmethod
    def _vocal_overlap_fraction(left, right, outgoing_start, incoming_start, duration):
        left_sections = TransitionPlanner._vocal_sections(left)
        right_sections = TransitionPlanner._vocal_sections(right)
        if not left_sections and not right_sections:
            density = (left.vocal_density or 0.5) * (right.vocal_density or 0.5)
            return min(0.85, density)
        samples = [index / 7 * duration for index in range(8)]
        overlap = 0
        for point in samples:
            left_vocal = any(a <= outgoing_start + point < b for a, b in left_sections)
            right_vocal = any(a <= incoming_start + point < b for a, b in right_sections)
            overlap += int(left_vocal and right_vocal)
        return overlap / len(samples)

    @staticmethod
    def _vocal_entries(analysis):
        if analysis.vocal_entry_points:
            return analysis.vocal_entry_points
        if TransitionPlanner._vocal_sections(analysis):
            return tuple(start for start, _end in TransitionPlanner._vocal_sections(analysis))
        return tuple(start for start, _end in TransitionPlanner._curve_sections(analysis))

    @staticmethod
    def _vocal_exits(analysis):
        if analysis.vocal_exit_points:
            return analysis.vocal_exit_points
        if TransitionPlanner._vocal_sections(analysis):
            return tuple(end for _start, end in TransitionPlanner._vocal_sections(analysis))
        return tuple(end for _start, end in TransitionPlanner._curve_sections(analysis))

    @staticmethod
    def _curve_sections(analysis):
        curve = analysis.vocal_curve
        duration = analysis.duration
        if not curve or duration <= 0:
            return ()
        active = [value >= 0.58 for value in curve]
        sections = []
        start = None
        for index, is_active in enumerate(active + [False]):
            if is_active and start is None:
                start = index / len(active) * duration
            elif not is_active and start is not None:
                end = index / len(active) * duration
                if end - start >= 0.45:
                    sections.append((max(0.0, start - 0.18), min(duration, end + 0.18)))
                start = None
        return tuple(sections)

    @staticmethod
    def _vocal_sections(analysis):
        return analysis.vocal_sections or TransitionPlanner._curve_sections(analysis)

    @staticmethod
    def _point_score(point, points, tolerance):
        if not points:
            return 0.0
        return max(0.0, 1.0 - min(abs(point - value) for value in points) / tolerance)

    @staticmethod
    def _intro_skip_penalty(right, incoming_start):
        """Penalize retaining a long, low-value intro before a strong entry."""
        entries = TransitionPlanner._vocal_entries(right)
        if not entries or incoming_start > min(entries) + 0.8:
            return 0.0
        first_entry = min(entries)
        if first_entry <= 1.5 or incoming_start >= first_entry - 0.8:
            return 0.0
        skipped = min(1.0, (first_entry - incoming_start) / max(6.0, first_entry))
        # A structural/energetic intro can be valuable; reduce the penalty if
        # it contains phrase boundaries or a meaningful energy rise.
        intro_value = 0.0
        intro_boundaries = [value for value in right.phrase_boundaries if 0 <= value < first_entry]
        if intro_boundaries:
            intro_value += 0.35
        curve = right.energy_curve
        if curve:
            split = max(1, min(len(curve), int(first_entry / max(right.duration, 1.0) * len(curve))))
            intro_mean = sum(curve[:split]) / split
            later_mean = sum(curve[split:]) / max(1, len(curve) - split)
            if intro_mean >= 0.58 or later_mean - intro_mean < 0.08:
                intro_value += 0.35
        if right.intro_silence > 0:
            intro_value -= 0.15
        return max(0.0, min(1.0, skipped * (1.0 - intro_value)))

    @staticmethod
    def _candidate_reason(candidate, scores):
        if candidate["mode"] == "vocal_handoff":
            return "clean outgoing vocal end; incoming lyric phrase starts on structural boundary"
        if candidate["incoming_kind"] == "strong_vocal_entry":
            return "strong vocal entry on a structural boundary"
        if candidate["incoming_kind"] == "vocal_entry":
            return "vocal entry point"
        if scores["intro_skip_penalty"] > 0.35:
            return "long instrumental intro before available vocal entry"
        if scores["vocal_overlap_penalty"] > 0.35:
            return "vocal overlap penalty"
        if candidate["incoming_kind"] in ("intro_start", "instrumental_blend"):
            return "instrumental blend"
        return "phrase/rhythm candidate"

    @staticmethod
    def _harmonic_score(left_key, right_key):
        if not left_key or not right_key:
            return 0.5
        left = str(left_key).strip().upper().replace("♯", "#").replace("♭", "B")
        right = str(right_key).strip().upper().replace("♯", "#").replace("♭", "B")
        if left == right:
            return 1.0
        if left.rstrip(" M") == right.rstrip(" M"):
            return 0.85
        notes = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11}
        def root(value):
            return next((notes[key] for key in sorted(notes, key=len, reverse=True) if value.startswith(key)), None)
        a, b = root(left), root(right)
        if a is None or b is None:
            return 0.5
        distance = min((a - b) % 12, (b - a) % 12)
        return {1: 0.75, 2: 0.58, 5: 0.72, 7: 0.72}.get(distance, 0.28)

    @staticmethod
    def _unique_points(points):
        result = []
        for point in sorted(max(0.0, float(value)) for value in points):
            if not result or abs(result[-1] - point) > 0.15:
                result.append(point)
        return result

    @staticmethod
    def _unique_labeled_points(points):
        priority = {
            "strong_vocal_entry": 5,
            "vocal_entry": 4,
            "chorus_like_entry": 3,
            "phrase_boundary": 2,
            "downbeat": 1,
            "silence_end": 1,
            "intro_start": 0,
            "instrumental_blend": 0,
        }
        result = []
        for point, label in sorted((max(0.0, float(point)), label) for point, label in points):
            if result and abs(result[-1][0] - point) <= 0.15:
                # Preserve the most meaningful semantic label when several
                # detectors land on the same musical point.
                if priority.get(label, 0) > priority.get(result[-1][1], 0):
                    result[-1] = (point, label)
                continue
            result.append((point, label))
        return result

    @staticmethod
    def _select_semantic_values(values, minimum_spacing: float, maximum: int):
        selected = []
        for value in sorted({round(max(0.0, float(item)), 3) for item in values}):
            if not selected or value - selected[-1] >= minimum_spacing:
                selected.append(value)
            if len(selected) >= maximum:
                break
        # The end of the relevant window is often more useful than an early
        # repeated line; retain it if spacing allows.
        if values and selected:
            last = max(float(item) for item in values)
            if last - selected[-1] >= minimum_spacing:
                selected[-1] = round(last, 3)
        return tuple(selected)

    @classmethod
    def _semantic_incoming_points(cls, right):
        limit = min(36.0, right.duration or 36.0)
        points = [(0.0, "intro_start")]
        entries = [value for value in cls._vocal_entries(right) if 0 < value <= limit]
        entries = cls._select_semantic_values(entries, minimum_spacing=1.5, maximum=8)
        phrase_boundaries = [value for value in right.phrase_boundaries if 0 < value <= limit]
        phrase_boundaries = cls._select_semantic_values(phrase_boundaries, minimum_spacing=3.0, maximum=8)
        for value in entries:
            near_phrase = any(abs(value - boundary) <= 0.8 for boundary in phrase_boundaries)
            label = "strong_vocal_entry" if near_phrase else "vocal_entry"
            points.append((value, label))
        for value in phrase_boundaries:
            level = cls._curve_at(right.energy_curve, value / max(right.duration, 1.0))
            points.append((value, "chorus_like_entry" if level >= 0.68 else "phrase_boundary"))

        # Downbeats are useful as alignment anchors, but only the first few
        # semantic ones are considered.  Every beat is intentionally excluded.
        downbeats = [value for value in right.downbeats if 0 < value <= limit]
        for value in cls._select_semantic_values(downbeats, minimum_spacing=3.0, maximum=6):
            points.append((value, "downbeat"))
        if right.intro_silence > 0:
            points.append((min(right.intro_silence, 8.0), "silence_end"))
        return cls._unique_labeled_points(points)

    @staticmethod
    def _fallback(left, right, reason):
        return TransitionPlan(current_path=left.path, next_path=right.path, duration=2.0,
                              mode="fallback", smart_eq=False, confidence=0.0,
                              reason=reason, auto_dj=False)

    @staticmethod
    def _loudness_gain(lufs, peak_db):
        if lufs is None or not math.isfinite(lufs):
            return 1.0
        gain = 10 ** ((-14.0 - lufs) / 20.0)
        if peak_db is not None and peak_db + 20 * math.log10(max(gain, 1e-6)) > -1.0:
            gain *= 10 ** ((-1.0 - peak_db) / 20.0) / max(gain, 1e-6)
        return max(0.72, min(1.18, gain))

    @staticmethod
    def _eq_strength(left, right, style):
        energy_gap = abs((left.energy or 0.5) - (right.energy or 0.5))
        return min(0.9, 0.35 + energy_gap * 0.8 + {"subtle": 0.0, "balanced": 0.1, "energetic": 0.2}[style])
