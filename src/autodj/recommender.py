# recommender.py
#
# Copyright 2026 Lluciocc (llucio.cc00@gmail.com)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import math
import random
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Protocol

from .planner import harmonic_compatibility

_GENRE_SEPARATOR = re.compile(r"\s*(?:[/;,|&+]|\band\b|\bet\b)\s*", re.IGNORECASE)
_PUNCTUATION = re.compile(r"[^a-z0-9]+")
_GENERIC_GENRES = {
    "",
    "genre",
    "inconnu",
    "misc",
    "miscellaneous",
    "other",
    "unknown",
    "unknown genre",
    "various",
}


class RandomSource(Protocol):
    def random(self) -> float: ...

    def choice(self, sequence): ...


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    track: Any
    score: float
    similarity_score: float
    transition_score: float | None = None
    plan: Any | None = None


def normalize_text(value: object) -> str:
    """Fold case, accents and punctuation for metadata comparisons."""
    folded = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = "".join(character for character in folded if not unicodedata.combining(character))
    return _PUNCTUATION.sub(" ", ascii_value.casefold()).strip()


def split_genres(value: object) -> frozenset[str]:
    genres = {
        normalize_text(part)
        for part in _GENRE_SEPARATOR.split(str(value or ""))
        if normalize_text(part) not in _GENERIC_GENRES
    }
    return frozenset(genres)


def genre_compatibility(left: object, right: object) -> float | None:
    first, second = split_genres(left), split_genres(right)
    if not first or not second:
        return None
    shared = first & second
    if shared:
        # Sørensen-Dice rewards an exact set while still giving a strong
        # result when just one genre in a multi-valued field overlaps.
        return min(1.0, 0.72 + 0.28 * (2 * len(shared) / (len(first) + len(second))))
    if any(a in b or b in a for a in first for b in second):
        return 0.55
    return 0.0


def bpm_compatibility(left: float | None, right: float | None) -> float | None:
    """Score the closest normal, half-time or double-time tempo relation."""
    try:
        a, b = float(left or 0), float(right or 0)
    except (TypeError, ValueError):
        return None
    if a <= 0 or b <= 0:
        return None
    difference = min(abs(a - b * factor) / a for factor in (0.5, 1.0, 2.0))
    # Four percent is the transition planner's conservative stretch range.
    # Nearby tempos remain useful for phrase transitions, then decay quickly.
    if difference <= 0.04:
        return 1.0 - difference * 5.0
    if difference <= 0.12:
        return 0.80 - (difference - 0.04) * 7.5
    return max(0.0, 0.20 - (difference - 0.12))


def energy_compatibility(left: Any | None, right: Any | None) -> float | None:
    outgoing = _curve_value(left, 0.92)
    incoming = _curve_value(right, 0.10)
    if outgoing is None or incoming is None:
        return None
    change = incoming - outgoing
    # Changes up to roughly 10% form a natural contour. Larger jumps are
    # progressively penalized, without becoming a hard exclusion.
    if -0.10 <= change <= 0.12:
        return 1.0 - abs(change - 0.02) * 1.4
    return max(0.0, 0.86 - (abs(change) - 0.10) * 1.65)


def _curve_value(analysis: Any | None, position: float) -> float | None:
    if analysis is None:
        return None
    curve = tuple(getattr(analysis, "energy_curve", ()) or ())
    if curve:
        index = min(len(curve) - 1, max(0, round(position * (len(curve) - 1))))
        return float(curve[index])
    energy = getattr(analysis, "energy", None)
    return float(energy) if energy is not None else None


class AutoDJRecommender:
    """Rank local tracks without any dependency on GTK or audio playback."""

    KEY_CONFIDENCE_THRESHOLD = 0.25
    IMMEDIATE_HISTORY = 8

    def __init__(self, rng: RandomSource | None = None) -> None:
        self.rng = rng or random.Random()

    def preselect(
        self,
        current: Any,
        recently_played: Iterable[Any],
        queue: Iterable[Any],
        library: Iterable[Any],
        limit: int = 12,
    ) -> list[ScoredCandidate]:
        """Cheap metadata-only pass used before any detailed analysis."""
        return self.rank(
            current,
            recently_played,
            queue,
            library,
            analyses={},
            transition_plans={},
            limit=limit,
        )

    def rank(
        self,
        current: Any,
        recently_played: Iterable[Any],
        queue: Iterable[Any],
        library: Iterable[Any],
        analyses: Mapping[str, Any] | None = None,
        transition_plans: Mapping[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[ScoredCandidate]:
        analyses = analyses or {}
        transition_plans = transition_plans or {}
        recent = list(recently_played)
        queue_paths = {str(getattr(track, "path", "")) for track in queue}
        current_path = str(getattr(current, "path", ""))
        immediate_paths = {
            str(getattr(track, "path", "")) for track in recent[: self.IMMEDIATE_HISTORY]
        }
        pool = [
            track
            for track in library
            if str(getattr(track, "path", "")) not in queue_paths
            and str(getattr(track, "path", "")) != current_path
        ]
        strict = [track for track in pool if str(getattr(track, "path", "")) not in immediate_paths]
        # Small libraries eventually relax only the history exclusion. The
        # current track and queued tracks remain absolute exclusions.
        candidates = strict or pool
        scored = [
            self._score(current, candidate, recent, analyses, transition_plans)
            for candidate in candidates
        ]
        scored.sort(
            key=lambda item: (
                item.score,
                -int(getattr(item.track, "play_count", 0) or 0),
                normalize_text(getattr(item.track, "title", "")),
                str(getattr(item.track, "path", "")),
            ),
            reverse=True,
        )
        return scored[: max(0, limit)] if limit is not None else scored

    def choose(self, *args, **kwargs) -> ScoredCandidate | None:
        kwargs.pop("limit", None)
        ranked = self.rank(*args, limit=None, **kwargs)
        if not ranked:
            return None
        best = ranked[0].score
        close = [candidate for candidate in ranked if best - candidate.score <= 0.035]
        # Controlled exploration only occurs among musically near-equivalent
        # candidates and remains deterministic with an injected RNG.
        return self.rng.choice(close)

    def safe_fallback(
        self,
        current: Any,
        recently_played: Iterable[Any],
        queue: Iterable[Any],
        library: Iterable[Any],
    ) -> ScoredCandidate | None:
        """Last-resort random choice with the non-negotiable exclusions."""
        current_path = str(getattr(current, "path", ""))
        queue_paths = {str(getattr(track, "path", "")) for track in queue}
        recent_paths = {
            str(getattr(track, "path", ""))
            for track in list(recently_played)[: self.IMMEDIATE_HISTORY]
        }
        pool = [
            track
            for track in library
            if str(getattr(track, "path", "")) != current_path
            and str(getattr(track, "path", "")) not in queue_paths
        ]
        preferred = [track for track in pool if str(getattr(track, "path", "")) not in recent_paths]
        if not (preferred or pool):
            return None
        return ScoredCandidate(self.rng.choice(preferred or pool), 0.0, 0.0)

    def _score(
        self,
        current: Any,
        candidate: Any,
        recent: list[Any],
        analyses: Mapping[str, Any],
        transition_plans: Mapping[str, Any],
    ) -> ScoredCandidate:
        path = str(getattr(candidate, "path", ""))
        left = analyses.get(str(getattr(current, "path", "")))
        right = analyses.get(path)
        signals: list[tuple[float, float]] = []

        genre = genre_compatibility(getattr(current, "genre", ""), getattr(candidate, "genre", ""))
        if genre is not None:
            signals.append((0.27, genre))
        tempo = bpm_compatibility(getattr(left, "bpm", None), getattr(right, "bpm", None))
        if tempo is not None:
            signals.append((0.18, tempo))
        key = self._key_score(left, right)
        if key is not None:
            signals.append((0.12, key))
        energy = energy_compatibility(left, right)
        if energy is not None:
            signals.append((0.17, energy))

        artist = normalize_text(getattr(candidate, "artist", ""))
        current_artist = normalize_text(getattr(current, "artist", ""))
        artist_score = 0.62
        if artist and artist == current_artist:
            artist_score = 0.72
        recent_artists = [normalize_text(getattr(track, "artist", "")) for track in recent[:6]]
        repeats = sum(1 for value in recent_artists if artist and value == artist)
        artist_score -= min(0.62, repeats * 0.24)

        album = normalize_text(getattr(candidate, "album", ""))
        current_album = normalize_text(getattr(current, "album", ""))
        album_score = 0.65
        if album and album == current_album:
            album_score -= 0.38
            left_number = int(getattr(current, "track_number", 0) or 0)
            right_number = int(getattr(candidate, "track_number", 0) or 0)
            if left_number and abs(left_number - right_number) == 1:
                album_score -= 0.20
        signals.extend(((0.12, max(0.0, artist_score)), (0.07, max(0.0, album_score))))

        played = max(0, int(getattr(candidate, "play_count", 0) or 0))
        discovery = max(0.25, 1.0 - math.log1p(played) / 8.0)
        recent_index = next(
            (
                index
                for index, track in enumerate(recent)
                if str(getattr(track, "path", "")) == path
            ),
            None,
        )
        if recent_index is not None:
            discovery *= min(0.55, 0.12 + recent_index * 0.05)
        last_played = self._last_played_score(getattr(candidate, "last_played", None))
        discovery *= last_played
        signals.append((0.07, discovery))

        weight = sum(item[0] for item in signals)
        similarity = sum(signal_weight * value for signal_weight, value in signals) / weight
        plan = transition_plans.get(path)
        transition = None
        if plan is not None:
            transition = max(0.0, min(1.0, float(getattr(plan, "confidence", 0.0) or 0.0)))
        score = similarity if transition is None else similarity * 0.76 + transition * 0.24
        # Tiny seeded jitter prevents permanent lock-in without overcoming a
        # meaningful musical-score difference.
        score += (self.rng.random() - 0.5) * 0.018
        return ScoredCandidate(candidate, score, similarity, transition, plan)

    @classmethod
    def _key_score(cls, left: Any | None, right: Any | None) -> float | None:
        if left is None or right is None:
            return None
        confidence = min(
            float(getattr(left, "key_confidence", 0.0) or 0.0),
            float(getattr(right, "key_confidence", 0.0) or 0.0),
        )
        if confidence < cls.KEY_CONFIDENCE_THRESHOLD:
            return None
        compatibility = harmonic_compatibility(
            getattr(left, "key", None), getattr(right, "key", None)
        )
        return 0.5 + (compatibility - 0.5) * confidence

    @staticmethod
    def _last_played_score(value: object) -> float:
        if not value:
            return 1.0
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            days = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400)
        except (TypeError, ValueError):
            return 1.0
        return min(1.0, 0.30 + days / 35.0)
