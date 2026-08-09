# planner.py
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
    strategy: str = "fallback"
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
    candidate_score: float = 0.0
    reason: str = "conservative fallback"
    auto_dj: bool = True


class TransitionPlanner:
    """Choose a safe plan from several musically meaningful candidates."""

    STYLE_BARS = {"subtle": (1, 2), "balanced": (2, 4), "energetic": (4, 8)}
    KEY_CONFIDENCE_THRESHOLD = 0.25
    SCORE_WEIGHTS = {
        "rhythm": 0.12,
        "downbeat_alignment": 0.08,
        "phrase_alignment_score": 0.12,
        "tempo": 0.08,
        "energy": 0.08,
        "harmonic": 0.06,
        "vocal_score": 0.04,
        "silence": 0.02,
        "structure": 0.04,
        "entry_salience_score": 0.14,
        "exit_salience_score": 0.10,
        "strategy_suitability": 0.06,
        "beat_drift_score": 0.04,
        "vocal_handoff_score": 0.15,
        "intro_skip_penalty": -0.18,
        "outgoing_cut_penalty": -0.10,
        # Vocal-over-vocal is the strongest negative signal. A clean handoff
        # still wins, but a dense overlap must not be rescued by rhythm alone.
        "vocal_overlap_penalty": -0.25,
    }

    def plan(
        self, current, following, left: TrackAnalysis, right: TrackAnalysis, options: dict
    ) -> TransitionPlan:
        style = options.get("style", "balanced")
        if style not in self.STYLE_BARS:
            style = "balanced"
        description = " ".join(
            str(value or "")
            for value in (
                getattr(current, "genre", ""),
                getattr(current, "album", ""),
                getattr(following, "genre", ""),
                getattr(following, "album", ""),
            )
        ).lower()
        if any(word in description for word in ("podcast", "audiobook", "spoken word")):
            return self._fallback(
                left, right, "spoken content: beat matching and bass swap disabled"
            )
        if any(
            word in description for word in ("classical", "live", "continuous", "dj mix", "mixtape")
        ):
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
            left.bpm or 0.0,
            left.source_bpm,
            left.beat_confidence,
            left.tempo_stability,
            len(left.beats),
            len(left.downbeats),
            len(left.phrase_boundaries),
            right.bpm or 0.0,
            right.source_bpm,
            right.beat_confidence,
            right.tempo_stability,
            len(right.beats),
            len(right.downbeats),
            len(right.phrase_boundaries),
            left.key or "unknown",
            right.key or "unknown",
            left.key_confidence,
            right.key_confidence,
            left.energy or 0,
            right.energy or 0,
            left.vocal_density or 0,
            right.vocal_density or 0,
            left.lyrics_source or "none",
            right.lyrics_source or "none",
            len(self._vocal_entries(left)),
            len(self._vocal_entries(right)),
            len(self._vocal_exits(left)),
            len(self._vocal_exits(right)),
            tempo_factor,
            abs(tempo_factor - 1),
        )
        candidates = self._candidates(left, right, style, options, tempo_factor, beat_cap)
        LOGGER.info(
            "AutoDJ semantic points outgoing=%s incoming=%s",
            [(round(value, 2), label) for value, label in self._semantic_outgoing_points(left)],
            [(round(value, 2), label) for value, label in self._semantic_incoming_points(right)],
        )
        LOGGER.info(
            "AutoDJ generated %d candidates for %s -> %s", len(candidates), left.path, right.path
        )
        if not candidates:
            return self._fallback(left, right, tempo_reason or "no compatible musical candidate")

        scored = []
        for candidate in candidates:
            scores = self._score_candidate(candidate, left, right, tempo_factor)
            total = sum(self.SCORE_WEIGHTS[name] * scores[name] for name in self.SCORE_WEIGHTS)
            confidence = self._confidence(total, left, right, candidate, scores)
            candidate["scores"] = scores
            candidate["total"] = total
            candidate["confidence"] = confidence
            candidate["selected_strategy"] = scores["strategy"]
            LOGGER.debug(
                "AutoDJ candidate %s incoming_start=%.2f score=%.2f outgoing_start=%.2f duration=%.2f "
                "scores=%s reason=%r",
                candidate["selected_strategy"],
                candidate["incoming_start"],
                total,
                candidate["outgoing_start"],
                candidate["duration"],
                {
                    key: round(value, 3)
                    for key, value in scores.items()
                    if isinstance(value, (int, float))
                },
                self._candidate_reason(candidate, scores),
            )
            scored.append(candidate)
        ranked = sorted(scored, key=self._ranking_key, reverse=True)
        for index, candidate in enumerate(ranked[:5], 1):
            LOGGER.info(
                "AutoDJ top_candidate rank=%d strategy=%s score=%.3f confidence=%.3f "
                "A@%.2f B@%.2f duration=%.2f entry_salience=%.2f exit_salience=%.2f reason=%r",
                index,
                candidate.get("selected_strategy", candidate["strategy"]),
                candidate["total"],
                candidate["confidence"],
                candidate["outgoing_start"],
                candidate["incoming_start"],
                candidate["duration"],
                candidate["scores"]["entry_salience_score"],
                candidate["scores"]["exit_salience_score"],
                self._candidate_reason(candidate, candidate["scores"]),
            )
        best = ranked[0]
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
            reason_parts = [
                "clean outgoing vocal end",
                "incoming vocal phrase starts on structural boundary",
            ]
        elif best["incoming_start"] > 2.0 and self._vocal_entries(right):
            reason_parts.append(
                "weak instrumental intro skipped to a stronger vocal entry"
                if scores["intro_skip_penalty"] >= 0.25
                else "intro skipped to a stronger semantic entry"
            )
        elif best["incoming_start"] <= 1.0:
            reason_parts.append("instrumental blend retained")
        if best.get("selected_strategy") not in (None, "clean_blend", "vocal_handoff"):
            reason_parts.append(f"{best['selected_strategy']} applied")
        if scores["outgoing_cut_penalty"] > 0.2:
            reason_parts.append("low-value outgoing tail omitted")
        if phrase_quality >= 0.7:
            reason_parts.append("aligned phrase boundaries")
        if vocal_quality >= 0.75:
            reason_parts.append("low vocal overlap")
        elif vocal_quality < 0.45:
            reason_parts.append("vocal overlap accepted for stronger rhythm")
        if harmonic_quality >= 0.75:
            if self._keys_confident(left, right):
                reason_parts.append("harmonically compatible")
        elif harmonic_quality < 0.45:
            if self._keys_confident(left, right):
                reason_parts.append("key clash softened by conservative blend")
        if tempo_reason and tempo_reason != "beats compatible":
            reason_parts.insert(0, tempo_reason)
        reason = "; ".join(reason_parts)
        plan = TransitionPlan(
            current_path=left.path,
            next_path=right.path,
            duration=round(best["duration"], 3),
            mode=best["mode"],
            strategy=best.get("selected_strategy", best["strategy"]),
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
            candidate_score=round(best["total"], 3),
            reason=reason,
        )
        LOGGER.info(
            "AutoDJ selected strategy=%s mode=%s A@%.2f -> B@%.2f duration=%.3f bars=%d beats=%d "
            "score=%.3f confidence=%.2f drift=%.2f reason=%r",
            plan.strategy,
            plan.mode,
            plan.outgoing_start,
            plan.incoming_start,
            plan.duration,
            plan.bars_used,
            plan.beats_used,
            plan.candidate_score,
            plan.confidence,
            best["scores"]["beat_drift_score"],
            plan.reason,
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
        return (
            ratio,
            "beats compatible" if delta <= 0.01 else "slight tempo stretch available",
            True,
        )

    def _candidates(self, left, right, style, options, tempo_factor, beat_cap):
        available = (
            min(value for value in (left.duration, right.duration) if value > 0)
            if left.duration and right.duration
            else 60.0
        )
        custom = options.get("length", "automatic")
        if custom != "automatic":
            try:
                lengths = [max(1.5, min(float(custom), available))]
            except (TypeError, ValueError):
                lengths = []
        elif beat_cap and left.bpm:
            lengths = [bars * 4 * 60.0 / left.bpm for bars in self.STYLE_BARS[style]]
        else:
            minimum, maximum = {
                "subtle": (2.0, 4.5),
                "balanced": (3.0, 9.5),
                "energetic": (6.0, 14.0),
            }[style]
            lengths = [
                min(maximum, max(minimum, available * fraction)) for fraction in (0.16, 0.22, 0.30)
            ]
        incoming_points = self._semantic_incoming_points(right)
        outgoing_semantic = self._semantic_outgoing_points(left)
        vocal_exits = [
            value
            for value in self._vocal_exits(left)
            if max(1.5, 0.0) <= left.duration - value <= 14.0
        ]
        vocal_exits = self._select_semantic_values(vocal_exits, minimum_spacing=1.5, maximum=4)
        result = []
        for duration in lengths:
            if duration > available:
                continue
            outgoing_points = [
                (
                    max(0.0, (left.duration or duration) - duration),
                    "final_tail",
                    left.duration or duration,
                )
            ]
            for outgoing_start, outgoing_kind in outgoing_semantic:
                outgoing_end = outgoing_start + duration
                if (
                    outgoing_end <= left.duration
                    and abs(outgoing_start - (left.duration - duration)) > 0.2
                ):
                    outgoing_points.append((outgoing_start, outgoing_kind, outgoing_end))
            for outgoing_start, outgoing_kind, outgoing_end in outgoing_points:
                bars = round(duration * (left.bpm or 0) / 60 / 4) if left.bpm and beat_cap else 0
                beats = (
                    max(0, round(duration * (left.bpm or 0) / 60)) if left.bpm and beat_cap else 0
                )
                for incoming_start, incoming_kind in incoming_points:
                    if outgoing_kind == "vocal_exit" and incoming_kind in (
                        "vocal_entry",
                        "strong_vocal_entry",
                    ):
                        continue
                    if right.duration and incoming_start + duration > right.duration - 0.05:
                        continue
                    mode = (
                        "beat_tempo"
                        if beat_cap and abs(tempo_factor - 1) > 0.01
                        else (
                            "beat"
                            if beat_cap
                            else ("phrase" if options.get("phrase_matching", True) else "gentle")
                        )
                    )
                    result.append(
                        {
                            "duration": duration,
                            "outgoing_start": outgoing_start,
                            "outgoing_end": outgoing_end,
                            "incoming_start": incoming_start,
                            "incoming_end": incoming_start + duration,
                            "bars_used": bars,
                            "beats_used": beats,
                            "mode": mode,
                            "incoming_kind": incoming_kind,
                            "outgoing_kind": outgoing_kind,
                            "strategy": "instrumental_blend"
                            if incoming_kind in ("intro_start", "instrumental_blend")
                            else incoming_kind,
                            "beat_offset": self._phase_offset(
                                left.beats, outgoing_start, right.beats, incoming_start
                            ),
                            "downbeat_offset": self._phase_offset(
                                left.downbeats, outgoing_start, right.downbeats, incoming_start
                            ),
                        }
                    )
            # A lyric/audio vocal exit is a separate family of candidates. Its
            # duration is allowed to be the actual tail length, rather than
            # forcing the tail into the style's preferred bar count.
            for vocal_exit in vocal_exits:
                vocal_duration = left.duration - vocal_exit
                if not 1.5 <= vocal_duration <= min(14.0, available):
                    continue
                vocal_bars = (
                    round(vocal_duration * (left.bpm or 0) / 60 / 4) if left.bpm and beat_cap else 0
                )
                vocal_beats = (
                    round(vocal_duration * (left.bpm or 0) / 60) if left.bpm and beat_cap else 0
                )
                for incoming_start, incoming_kind in incoming_points:
                    if incoming_kind not in ("vocal_entry", "strong_vocal_entry"):
                        continue
                    if right.duration and incoming_start + vocal_duration > right.duration - 0.05:
                        continue
                    result.append(
                        {
                            "duration": vocal_duration,
                            "outgoing_start": vocal_exit,
                            "outgoing_end": left.duration or vocal_duration,
                            "incoming_start": incoming_start,
                            "incoming_end": incoming_start + vocal_duration,
                            "bars_used": vocal_bars,
                            "beats_used": vocal_beats,
                            "mode": "vocal_handoff",
                            "incoming_kind": incoming_kind,
                            "outgoing_kind": "vocal_exit",
                            "strategy": "vocal_handoff",
                            "beat_offset": self._phase_offset(
                                left.beats, vocal_exit, right.beats, incoming_start
                            ),
                            "downbeat_offset": self._phase_offset(
                                left.downbeats, vocal_exit, right.downbeats, incoming_start
                            ),
                        }
                    )
        return self._deduplicate_candidates(result)

    @staticmethod
    def _deduplicate_candidates(candidates):
        unique = {}
        for candidate in candidates:
            key = (
                candidate.get("strategy", ""),
                round(candidate.get("outgoing_start", 0.0), 2),
                round(candidate.get("incoming_start", 0.0), 2),
                round(candidate.get("duration", 0.0), 2),
            )
            unique.setdefault(key, candidate)
        return list(unique.values())

    @staticmethod
    def _score_candidate(candidate, left, right, tempo_factor):
        phrase = (
            min(
                1.0,
                0.5
                * TransitionPlanner._boundary_score(
                    left.phrase_boundaries, candidate["outgoing_start"]
                )
                + 0.5
                * TransitionPlanner._boundary_score(
                    right.phrase_boundaries, candidate["incoming_start"]
                ),
            )
            if left.phrase_boundaries or right.phrase_boundaries
            else 0.32
        )
        rhythm = (
            min(left.beat_confidence, right.beat_confidence)
            if candidate["mode"].startswith("beat")
            else 0.42
        )
        if left.beats and right.beats:
            rhythm = min(
                1.0, rhythm * 0.55 + (1 - min(1.0, abs(candidate["beat_offset"]) / 0.18)) * 0.45
            )
        energy = 1.0 - min(
            1.0,
            abs(
                TransitionPlanner._curve_at(left.energy_curve, 0.94)
                - TransitionPlanner._curve_at(right.energy_curve, 0.10)
            ),
        )
        harmonic = TransitionPlanner._harmonic_score(
            left.key if TransitionPlanner._key_is_confident(left) else None,
            right.key if TransitionPlanner._key_is_confident(right) else None,
        )
        overlap = TransitionPlanner._vocal_overlap_fraction(
            left,
            right,
            candidate["outgoing_start"],
            candidate["incoming_start"],
            candidate["duration"],
        )
        vocal = 1.0 - overlap
        outgoing_exit = TransitionPlanner._point_score(
            candidate["outgoing_start"], TransitionPlanner._vocal_exits(left), 0.55
        )
        incoming_entry = TransitionPlanner._point_score(
            candidate["incoming_start"], TransitionPlanner._vocal_entries(right), 0.55
        )
        phrase_alignment = min(
            1.0,
            0.55
            * TransitionPlanner._boundary_score(left.phrase_boundaries, candidate["outgoing_start"])
            + 0.45
            * TransitionPlanner._boundary_score(
                right.phrase_boundaries, candidate["incoming_start"]
            ),
        )
        downbeat_alignment = min(
            1.0,
            0.5 * TransitionPlanner._boundary_score(left.downbeats, candidate["outgoing_start"])
            + 0.5 * TransitionPlanner._boundary_score(right.downbeats, candidate["incoming_start"]),
        )
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
                0.34 * incoming_entry + 0.26 * vocal + 0.24 * phrase_alignment + 0.16 * energy
            )
        else:
            vocal_handoff = 0.0
        entry_salience = TransitionPlanner._entry_salience(
            right, candidate["incoming_start"], candidate["incoming_kind"]
        )
        exit_salience = TransitionPlanner._exit_salience(
            left, candidate["outgoing_start"], candidate.get("outgoing_kind", "")
        )
        intro_value = TransitionPlanner._intro_value_score(right)
        intro_skip = TransitionPlanner._intro_skip_penalty(right, candidate["incoming_start"])
        outgoing_cut = TransitionPlanner._outgoing_cut_penalty(
            left, candidate["outgoing_start"], candidate.get("outgoing_end")
        )
        silence = min(
            1.0, 0.5 + min(left.outro_silence, 1.0) * 0.25 + min(right.intro_silence, 1.0) * 0.25
        )
        structure = phrase * 0.7 + silence * 0.3
        tempo = max(0.0, 1.0 - abs(tempo_factor - 1.0) / 0.08) if left.bpm and right.bpm else 0.35
        drift = TransitionPlanner._beat_drift_score(left, right, candidate, tempo_factor)
        strategy = TransitionPlanner._strategy_for_candidate(
            candidate, vocal_handoff, overlap, rhythm, phrase_alignment, entry_salience, left, right
        )
        strategy_suitability = TransitionPlanner._strategy_suitability(
            strategy, candidate, left, right
        )
        return {
            "rhythm": rhythm,
            "downbeat_alignment": downbeat_alignment,
            "phrase_alignment_score": phrase_alignment,
            "tempo": tempo,
            "energy": energy,
            "harmonic": harmonic,
            "vocal_score": vocal,
            "silence": silence,
            "structure": structure,
            "entry_salience_score": entry_salience,
            "exit_salience_score": exit_salience,
            "intro_value_score": intro_value,
            "outgoing_cut_penalty": outgoing_cut,
            "strategy_suitability": strategy_suitability,
            "beat_drift_score": drift,
            "vocal_handoff_score": vocal_handoff,
            "intro_skip_penalty": intro_skip,
            "vocal_overlap_penalty": overlap,
            "strategy": strategy,
        }

    @staticmethod
    def _confidence(total, left, right, candidate, scores=None):
        """Estimate absolute certainty, independent of candidate ranking."""
        scores = scores or {}
        beat = min(left.beat_confidence, right.beat_confidence)
        stability = min(left.tempo_stability, right.tempo_stability)
        phrase = scores.get("phrase_alignment_score", 0.25)
        energy = scores.get("energy", 0.35)
        vocal_evidence = (
            0.75
            if TransitionPlanner._vocal_sections(left) or TransitionPlanner._vocal_sections(right)
            else 0.25
        )
        key = (
            min(left.key_confidence, right.key_confidence)
            if TransitionPlanner._keys_confident(left, right)
            else 0.0
        )
        rhythm_evidence = beat if candidate["mode"].startswith("beat") else beat * 0.35
        evidence = (
            0.23 * rhythm_evidence
            + 0.17 * stability
            + 0.20 * phrase
            + 0.14 * energy
            + 0.12 * vocal_evidence
            + 0.09 * key
            + 0.05 * scores.get("beat_drift_score", 0.5)
        )
        quality = max(0.0, min(1.0, total))
        return max(0.0, min(1.0, 0.12 + 0.42 * quality + 0.58 * evidence))

    @classmethod
    def _ranking_key(cls, candidate):
        """Rank close scores by musical meaning before timestamp convenience."""
        scores = candidate["scores"]
        return (
            round(candidate["total"], 2),
            scores["entry_salience_score"],
            scores["vocal_handoff_score"],
            scores["phrase_alignment_score"],
            -scores["vocal_overlap_penalty"],
            -scores["intro_skip_penalty"],
            -candidate["incoming_start"],
        )

    @classmethod
    def _entry_salience(cls, analysis, point, label):
        labels = {
            "strong_vocal_entry": 0.98,
            "chorus_like_entry": 0.94,
            "energy_rise": 0.88,
            "vocal_entry": 0.82,
            "phrase_boundary": 0.58,
            "downbeat": 0.40,
            "silence_end": 0.34,
            "intro_start": 0.16,
            "instrumental_blend": 0.16,
        }
        score = labels.get(label, 0.35)
        normalized = point / max(analysis.duration, 1.0)
        current = cls._curve_at(analysis.energy_curve, normalized)
        before = cls._curve_at(analysis.energy_curve, max(0.0, normalized - 0.08))
        rise = max(0.0, current - before)
        if current >= 0.72:
            score += 0.10
        score += min(0.16, rise * 0.8)
        if cls._boundary_score(analysis.phrase_boundaries, point) >= 0.75:
            score += 0.08
        if cls._boundary_score(analysis.downbeats, point) >= 0.85:
            score += 0.04
        if label in ("intro_start", "instrumental_blend"):
            score = 0.18 + 0.30 * cls._intro_value_score(analysis)
        return max(0.0, min(1.0, score))

    @classmethod
    def _exit_salience(cls, analysis, point, label):
        if label in ("vocal_exit", "outro_boundary"):
            base = 0.90
        elif label in ("phrase_exit", "energy_drop"):
            base = 0.78
        elif label == "downbeat_exit":
            base = 0.48
        else:
            base = 0.32
        if cls._boundary_score(analysis.phrase_boundaries, point) >= 0.75:
            base += 0.10
        if analysis.outro_silence and point >= max(
            0.0, analysis.duration - analysis.outro_silence - 1.0
        ):
            base += 0.08
        return min(1.0, base)

    @classmethod
    def _intro_value_score(cls, analysis):
        entries = [point for point in cls._vocal_entries(analysis) if point > 0]
        boundaries = [point for point in analysis.phrase_boundaries if point > 0]
        # Phrase markers can exist inside an instrumental build.  Prefer the
        # first vocal as the meaningful end of the intro; only use a phrase
        # marker when there is no vocal evidence at all.
        first_event = min(entries, default=min(boundaries, default=min(analysis.duration, 8.0)))
        intro_length = min(30.0, first_event)
        if intro_length <= 0:
            return 0.0
        start = cls._curve_at(analysis.energy_curve, 0.0)
        end = cls._curve_at(analysis.energy_curve, intro_length / max(analysis.duration, 1.0))
        build = max(0.0, end - start)
        intro_boundaries = sum(1 for point in boundaries if point < first_event)
        rhythmic_value = min(1.0, intro_boundaries / 2.0)
        high_energy = cls._curve_at(
            analysis.energy_curve, min(1.0, intro_length / max(analysis.duration, 1.0))
        )
        short_intro = 1.0 if intro_length <= 4.0 else max(0.0, 1.0 - (intro_length - 4.0) / 20.0)
        silence_bonus = 0.0 if analysis.intro_silence else 0.08
        value = (
            0.30 * min(1.0, build * 3.0)
            + 0.25 * rhythmic_value
            + 0.20 * high_energy
            + 0.17 * short_intro
            + silence_bonus
        )
        return max(0.0, min(1.0, value))

    @classmethod
    def _first_meaningful_entry(cls, analysis):
        vocal_points = [point for point in cls._vocal_entries(analysis) if point > 0]
        if vocal_points:
            # Once reliable vocal evidence exists, the first phrase marker in
            # an instrumental build is not the entry we are trying to protect.
            return min(vocal_points)
        points = [point for point in analysis.phrase_boundaries if point > 0]
        for index in range(1, len(analysis.energy_curve or ())):
            if analysis.energy_curve[index] - analysis.energy_curve[index - 1] >= 0.18:
                points.append(index / len(analysis.energy_curve) * analysis.duration)
        return min(points) if points else 0.0

    @classmethod
    def _intro_skip_penalty(cls, right, incoming_start):
        """Cost retaining a weak intro when a salient entry is available."""
        first_entry = cls._first_meaningful_entry(right)
        if first_entry <= 1.5 or incoming_start >= first_entry - 0.8:
            return 0.0
        distance = min(1.0, (first_entry - incoming_start) / max(8.0, first_entry))
        salience = cls._entry_salience(
            right, first_entry, "vocal_entry" if cls._vocal_entries(right) else "phrase_boundary"
        )
        intro_value = cls._intro_value_score(right)
        # A demonstrated build/rhythmic intro is valuable enough to make
        # skipping it materially less attractive, while a flat intro keeps
        # nearly the full distance-based cost.
        return max(0.0, min(1.0, distance * (0.55 + 0.45 * salience) * (1.0 - 0.90 * intro_value)))

    @staticmethod
    def _outgoing_cut_penalty(left, outgoing_start, outgoing_end):
        if outgoing_end is None or outgoing_end >= left.duration - 0.75:
            return 0.0
        abandoned = max(0.0, left.duration - outgoing_end)
        return min(1.0, abandoned / max(8.0, left.duration * 0.18))

    @staticmethod
    def _key_is_confident(analysis):
        return bool(
            analysis.key and analysis.key_confidence >= TransitionPlanner.KEY_CONFIDENCE_THRESHOLD
        )

    @classmethod
    def _keys_confident(cls, left, right):
        return cls._key_is_confident(left) and cls._key_is_confident(right)

    @staticmethod
    def _beat_drift_score(left, right, candidate, tempo_factor):
        if not left.beats or not right.beats or not left.bpm or not right.bpm:
            return 0.45
        duration = candidate["duration"]
        left_start = min(left.beats, key=lambda value: abs(value - candidate["outgoing_start"]))
        right_start = min(right.beats, key=lambda value: abs(value - candidate["incoming_start"]))
        left_end = min(
            left.beats, key=lambda value: abs(value - (candidate["outgoing_start"] + duration))
        )
        right_end = min(
            right.beats, key=lambda value: abs(value - (candidate["incoming_start"] + duration))
        )
        left_error = abs((left_end - left_start) - duration)
        right_error = abs(
            (right_end - right_start) - duration * (left.bpm / right.bpm) / max(tempo_factor, 1e-6)
        )
        return max(0.0, min(1.0, 1.0 - (left_error + right_error) / 0.8))

    @staticmethod
    def _strategy_for_candidate(
        candidate, vocal_handoff, overlap, rhythm, phrase, entry, left, right
    ):
        if candidate["mode"] == "vocal_handoff":
            return "vocal_handoff"
        if (
            entry >= 0.93
            and phrase >= 0.85
            and candidate["incoming_kind"] in ("chorus_like_entry", "energy_rise")
        ):
            return "hard_cut"
        if (
            rhythm >= 0.9
            and left.tempo_stability >= 0.85
            and candidate.get("outgoing_kind") in ("downbeat_exit", "energy_drop")
            and candidate["duration"] <= 4.5
        ):
            return "beat_repeat_out"
        if (
            overlap >= 0.5
            and (left.vocal_density or 0.0) > 0.45
            and (right.vocal_density or 0.0) > 0.45
        ):
            return "filter_out"
        if entry >= 0.88 and rhythm >= 0.68 and phrase >= 0.65:
            return "bass_swap"
        if candidate.get("outgoing_kind") in ("vocal_exit", "phrase_exit") and phrase >= 0.65:
            return "echo_out"
        if candidate["mode"] == "gentle" and phrase >= 0.65:
            return "reverb_out"
        return "clean_blend"

    @staticmethod
    def _strategy_suitability(strategy, candidate, left, right):
        if strategy == "vocal_handoff":
            return (
                1.0 if candidate["incoming_kind"] in ("vocal_entry", "strong_vocal_entry") else 0.5
            )
        if strategy == "bass_swap":
            return min(left.beat_confidence, right.beat_confidence)
        if strategy == "filter_out":
            return 0.8
        if strategy in ("echo_out", "reverb_out"):
            return 0.7
        return 0.62

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
    def _candidate_reason(candidate, scores):
        if candidate["mode"] == "vocal_handoff":
            return "clean outgoing vocal end; incoming lyric phrase starts on structural boundary"
        if candidate["incoming_kind"] == "strong_vocal_entry":
            return "strong vocal entry on a structural boundary"
        if candidate["incoming_kind"] == "vocal_entry":
            return "vocal entry point"
        if candidate["incoming_kind"] in ("chorus_like_entry", "energy_rise"):
            return "strong instrumental/energy entry"
        if candidate.get("outgoing_kind") in ("phrase_exit", "energy_drop", "outro_boundary"):
            return "semantic outgoing boundary"
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
        notes = {
            "C": 0,
            "C#": 1,
            "DB": 1,
            "D": 2,
            "D#": 3,
            "EB": 3,
            "E": 4,
            "F": 5,
            "F#": 6,
            "GB": 6,
            "G": 7,
            "G#": 8,
            "AB": 8,
            "A": 9,
            "A#": 10,
            "BB": 10,
            "B": 11,
        }

        def root(value):
            return next(
                (
                    notes[key]
                    for key in sorted(notes, key=len, reverse=True)
                    if value.startswith(key)
                ),
                None,
            )

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
            "energy_rise": 3,
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
        phrase_boundaries = cls._select_semantic_values(
            phrase_boundaries, minimum_spacing=3.0, maximum=8
        )
        for value in entries:
            near_phrase = any(abs(value - boundary) <= 0.8 for boundary in phrase_boundaries)
            label = "strong_vocal_entry" if near_phrase else "vocal_entry"
            points.append((value, label))
        for value in phrase_boundaries:
            level = cls._curve_at(right.energy_curve, value / max(right.duration, 1.0))
            points.append((value, "chorus_like_entry" if level >= 0.68 else "phrase_boundary"))

        energy_points = []
        curve = right.energy_curve or ()
        for index in range(1, len(curve)):
            if curve[index] - curve[index - 1] >= 0.18:
                value = index / len(curve) * max(right.duration, 1.0)
                if 0 < value <= limit:
                    energy_points.append(value)
        for value in cls._select_semantic_values(energy_points, minimum_spacing=3.0, maximum=4):
            points.append((value, "energy_rise"))

        # Downbeats are useful as alignment anchors, but only the first few
        # semantic ones are considered.  Every beat is intentionally excluded.
        downbeats = [value for value in right.downbeats if 0 < value <= limit]
        for value in cls._select_semantic_values(downbeats, minimum_spacing=3.0, maximum=6):
            points.append((value, "downbeat"))
        if right.intro_silence > 0:
            points.append((min(right.intro_silence, 8.0), "silence_end"))
        points = cls._unique_labeled_points(points)
        priority = {
            "strong_vocal_entry": 6,
            "chorus_like_entry": 5,
            "energy_rise": 4,
            "vocal_entry": 4,
            "phrase_boundary": 3,
            "downbeat": 2,
            "silence_end": 1,
            "intro_start": 0,
        }
        selected = []
        for point in sorted(points, key=lambda item: (-priority.get(item[1], 0), item[0])):
            if point[0] == 0.0 or all(abs(point[0] - old[0]) >= 1.4 for old in selected):
                selected.append(point)
            if len(selected) >= 7:
                break
        if not any(point == 0.0 for point, _label in selected):
            selected.append((0.0, "intro_start"))
        return sorted(selected)

    @classmethod
    def _semantic_outgoing_points(cls, left):
        """Return a small set of meaningful places where A may hand off."""
        limit = max(0.0, left.duration - 32.0)
        points = []
        for value in cls._vocal_exits(left):
            if limit <= value <= left.duration - 1.5:
                points.append((value, "vocal_exit"))
        for value in left.phrase_boundaries:
            if limit <= value <= left.duration - 1.5:
                points.append((value, "phrase_exit"))
        for value in left.downbeats:
            if limit <= value <= left.duration - 1.5:
                points.append((value, "downbeat_exit"))
        curve = left.energy_curve or ()
        for index in range(1, len(curve)):
            if curve[index - 1] - curve[index] >= 0.18:
                value = index / len(curve) * max(left.duration, 1.0)
                if limit <= value <= left.duration - 1.5:
                    points.append((value, "energy_drop"))
        if left.outro_silence:
            value = max(0.0, left.duration - left.outro_silence)
            if limit <= value <= left.duration - 1.5:
                points.append((value, "outro_boundary"))
        points.sort(key=lambda item: (-cls._exit_salience(left, item[0], item[1]), item[0]))
        selected = []
        for value, label in points:
            if all(abs(value - old_value) >= 2.0 for old_value, _old_label in selected):
                selected.append((value, label))
            if len(selected) >= 2:
                break
        return tuple(sorted(selected))

    @staticmethod
    def _fallback(left, right, reason):
        return TransitionPlan(
            current_path=left.path,
            next_path=right.path,
            duration=2.0,
            mode="fallback",
            smart_eq=False,
            confidence=0.0,
            reason=reason,
            auto_dj=False,
        )

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
        return min(
            0.9, 0.35 + energy_gap * 0.8 + {"subtle": 0.0, "balanced": 0.1, "energetic": 0.2}[style]
        )
