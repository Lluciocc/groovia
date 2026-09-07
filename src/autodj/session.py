# session.py
#
# Copyright 2026 Lluciocc (llucio.cc00@gmail.com)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class PlaybackContext(str, Enum):
    """Origin of the active playback session.

    Only the existing automatic-library flow may grow its queue.  Transition
    planning deliberately does not use this value and remains available in
    every context.
    """

    LIBRARY_AUTOMATIC = "library-automatic"
    PLAYLIST = "playlist"
    ALBUM = "album"
    ARTIST = "artist"
    MANUAL_QUEUE = "manual-queue"
    DIRECT_TRACK = "direct-track"
    RESTORED = "restored"

    @property
    def allows_recommendations(self) -> bool:
        return self is PlaybackContext.LIBRARY_AUTOMATIC


@dataclass(frozen=True, slots=True)
class RecommendationToken:
    generation: int
    current_path: str
    queue_signature: tuple[tuple[str, bool], ...]
    context: PlaybackContext


class RecommendationGuard:
    """Issue and invalidate tokens for asynchronous recommendation work."""

    def __init__(self) -> None:
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    def invalidate(self) -> None:
        self._generation += 1

    def issue(
        self,
        current_path: str,
        queue_signature: Iterable[tuple[str, bool]],
        context: PlaybackContext,
    ) -> RecommendationToken:
        self._generation += 1
        return RecommendationToken(
            self._generation,
            current_path,
            tuple(queue_signature),
            context,
        )

    def accepts(
        self,
        token: RecommendationToken,
        current_path: str,
        queue_signature: Iterable[tuple[str, bool]],
        context: PlaybackContext,
    ) -> bool:
        return (
            token.generation == self._generation
            and token.current_path == current_path
            and token.queue_signature == tuple(queue_signature)
            and token.context is context
            and context.allows_recommendations
        )


class PlaybackEventDeduplicator:
    """Reject a repeated callback for the same concrete playback handoff."""

    def __init__(self) -> None:
        self._last_marker: object | None = None

    def accepts(self, marker: object) -> bool:
        if marker == self._last_marker:
            return False
        self._last_marker = marker
        return True


class QueueProvenance:
    """Track which visible queue objects were supplied by Auto DJ."""

    def __init__(self) -> None:
        self._automatic_ids: set[int] = set()

    def clear(self) -> None:
        self._automatic_ids.clear()

    def mark_automatic(self, track: object) -> None:
        self._automatic_ids.add(id(track))

    def discard(self, track: object) -> None:
        self._automatic_ids.discard(id(track))

    def is_automatic(self, track: object) -> bool:
        return id(track) in self._automatic_ids

    def prune(self, queue: Iterable[object]) -> None:
        live = {id(track) for track in queue}
        self._automatic_ids.intersection_update(live)

    def append_manual(self, queue: list, track: object) -> None:
        """Append after manual choices but before all automatic suggestions."""
        self.discard(track)
        index = next(
            (index for index, queued in enumerate(queue) if self.is_automatic(queued)),
            len(queue),
        )
        queue.insert(index, track)

    def signature(self, queue: Iterable) -> tuple[tuple[str, bool], ...]:
        self.prune(queue)
        return tuple((str(getattr(track, "path", "")), self.is_automatic(track)) for track in queue)
