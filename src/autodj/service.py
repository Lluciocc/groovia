# service.py
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
import threading
from concurrent.futures import ThreadPoolExecutor

from gi.repository import GLib

from .analysis import AnalysisCache, TrackAnalyzer
from .planner import TransitionPlanner
from .recommender import AutoDJRecommender

LOGGER = logging.getLogger("groovia.autodj")


class AutoDJService:
    def __init__(
        self,
        callback=None,
        data_dir=None,
        lyrics_provider=None,
        recommendation_callback=None,
        rng=None,
    ):
        self.callback = callback
        self.recommendation_callback = recommendation_callback
        self.analyzer = TrackAnalyzer(AnalysisCache(data_dir), lyrics_provider=lyrics_provider)
        self.planner = TransitionPlanner()
        self.recommender = AutoDJRecommender(rng=rng)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="groovia-autodj")
        self._recommendation_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="groovia-autodj-select"
        )
        self._lock = threading.Lock()
        self._generation = 0
        self._active_key = None
        self._completed_key = None
        self._recommendation_generation = 0
        self._active_recommendation_key = None

    @staticmethod
    def _prepare_key(current, following, options):
        values = tuple(sorted((str(key), repr(value)) for key, value in (options or {}).items()))
        return (current.path, following.path, values)

    def prepare(self, current, following, options=None):
        if not current or not following or current.path == following.path:
            return
        options = dict(options or {})
        key = self._prepare_key(current, following, options)
        with self._lock:
            if key == self._active_key or key == self._completed_key:
                LOGGER.debug(
                    "Auto DJ duplicate prepare skipped current=%s next=%s",
                    current.path,
                    following.path,
                )
                return
            self._generation += 1
            generation = self._generation
            self._active_key = key
            self._completed_key = None

        def worker():
            try:
                left = self.analyzer.analyze(current)
                right = self.analyzer.analyze(following)
                plan = self.planner.plan(current, following, left, right, options)
                LOGGER.info(
                    "transition ready current=%r next=%r strategy=%s mode=%s duration=%.3fs "
                    "outgoing_start=%.3f outgoing_end=%.3f incoming_start=%.3f bars=%d beats=%d "
                    "score=%.3f confidence=%.2f reason=%r",
                    getattr(current, "title", current.path),
                    getattr(following, "title", following.path),
                    plan.strategy,
                    plan.mode,
                    plan.duration,
                    plan.outgoing_start,
                    plan.outgoing_end,
                    plan.incoming_start,
                    plan.bars_used,
                    plan.beats_used,
                    plan.candidate_score,
                    plan.confidence,
                    plan.reason,
                )
            except Exception:
                # A plan is optional; playback must remain available even if a
                # decoder or an analyzer fails.
                LOGGER.exception(
                    "analysis/transition unavailable current=%r next=%r",
                    getattr(current, "title", current.path),
                    getattr(following, "title", following.path),
                )
                plan = None
            with self._lock:
                if generation == self._generation:
                    self._active_key = None
                    if plan is not None:
                        self._completed_key = key
            GLib.idle_add(self._deliver, generation, plan)

        self._executor.submit(worker)

    def cancel(self):
        with self._lock:
            self._generation += 1
            self._active_key = None
            self._completed_key = None
        LOGGER.debug("Auto DJ analysis cancelled generation=%d", self._generation)

    def recommend(
        self,
        current,
        recently_played,
        queue,
        library,
        options=None,
        *,
        limit=4,
        request_token=None,
    ):
        """Select future tracks in two stages without blocking GTK.

        The first pass only reads the supplied SQLite-backed metadata. At most
        twelve candidates then use the shared analysis cache and transition
        planner. ``request_token`` belongs to the window-level session guard
        and is returned untouched for a final staleness check.
        """
        if not current or limit <= 0:
            return
        recently_played = list(recently_played)
        queue = list(queue)
        library = list(library)
        options = dict(options or {})
        key = (
            current.path,
            tuple(track.path for track in queue),
            tuple(track.path for track in recently_played[:12]),
            int(limit),
        )
        with self._lock:
            if key == self._active_recommendation_key:
                return
            self._recommendation_generation += 1
            generation = self._recommendation_generation
            self._active_recommendation_key = key

        def worker():
            try:
                metadata_candidates = self.recommender.preselect(
                    current,
                    recently_played,
                    queue,
                    library,
                    limit=12,
                )
                candidates = [item.track for item in metadata_candidates]
                analyses = {}
                if candidates:
                    left = self.analyzer.analyze(current)
                    analyses[current.path] = left
                    for candidate in candidates:
                        try:
                            right = self.analyzer.analyze(candidate)
                            analyses[candidate.path] = right
                        except Exception:
                            LOGGER.warning(
                                "recommendation analysis unavailable current=%r candidate=%r",
                                getattr(current, "title", current.path),
                                getattr(candidate, "title", candidate.path),
                                exc_info=True,
                            )
                ranked = []
                remaining = list(candidates)
                anchor = current
                sequence_history = list(recently_played)
                while remaining and len(ranked) < limit:
                    plans = {}
                    left = analyses.get(anchor.path)
                    if left is not None:
                        for candidate in remaining:
                            right = analyses.get(candidate.path)
                            if right is None:
                                continue
                            try:
                                plans[candidate.path] = self.planner.plan(
                                    anchor, candidate, left, right, options
                                )
                            except Exception:
                                LOGGER.debug(
                                    "future transition scoring unavailable %r -> %r",
                                    getattr(anchor, "title", anchor.path),
                                    getattr(candidate, "title", candidate.path),
                                    exc_info=True,
                                )
                    step = self.recommender.rank(
                        anchor,
                        sequence_history,
                        [*queue, *(item.track for item in ranked)],
                        remaining,
                        analyses=analyses,
                        transition_plans=plans,
                        limit=1,
                    )
                    if not step:
                        break
                    selected = step[0]
                    ranked.append(selected)
                    remaining = [
                        candidate
                        for candidate in remaining
                        if candidate.path != selected.track.path
                    ]
                    sequence_history.insert(0, anchor)
                    anchor = selected.track
                LOGGER.info(
                    "recommendations ready current=%r candidates=%s selected=%s",
                    getattr(current, "title", current.path),
                    len(candidates),
                    [
                        (getattr(item.track, "title", item.track.path), round(item.score, 3))
                        for item in ranked
                    ],
                )
            except Exception:
                LOGGER.exception(
                    "recommendation unavailable current=%r",
                    getattr(current, "title", current.path),
                )
                # The final safe fallback is still constrained by the current
                # track, queue, and immediate-history rules in the recommender.
                try:
                    ranked = self.recommender.rank(
                        current,
                        recently_played,
                        queue,
                        library,
                        limit=limit,
                    )
                except Exception:
                    fallback = self.recommender.safe_fallback(
                        current, recently_played, queue, library
                    )
                    ranked = [fallback] if fallback else []
            with self._lock:
                if generation == self._recommendation_generation:
                    self._active_recommendation_key = None
            GLib.idle_add(
                self._deliver_recommendations,
                generation,
                request_token,
                ranked,
            )

        self._recommendation_executor.submit(worker)

    def cancel_recommendations(self):
        with self._lock:
            self._recommendation_generation += 1
            self._active_recommendation_key = None
        LOGGER.debug(
            "Auto DJ recommendation cancelled generation=%d",
            self._recommendation_generation,
        )

    def _deliver(self, generation, plan):
        with self._lock:
            current_generation = self._generation
        if generation == current_generation and plan is not None and self.callback:
            self.callback(plan)
        return GLib.SOURCE_REMOVE

    def _deliver_recommendations(self, generation, request_token, ranked):
        with self._lock:
            current_generation = self._recommendation_generation
        if generation == current_generation and self.recommendation_callback:
            self.recommendation_callback(request_token, ranked)
        return GLib.SOURCE_REMOVE

    def close(self):
        self.cancel()
        self.cancel_recommendations()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._recommendation_executor.shutdown(wait=False, cancel_futures=True)
