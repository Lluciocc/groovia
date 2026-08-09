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

LOGGER = logging.getLogger("groovia.autodj")


class AutoDJService:
    def __init__(self, callback=None, data_dir=None, lyrics_provider=None):
        self.callback = callback
        self.analyzer = TrackAnalyzer(AnalysisCache(data_dir), lyrics_provider=lyrics_provider)
        self.planner = TransitionPlanner()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="groovia-autodj")
        self._lock = threading.Lock()
        self._generation = 0
        self._active_key = None
        self._completed_key = None

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

    def _deliver(self, generation, plan):
        with self._lock:
            current_generation = self._generation
        if generation == current_generation and plan is not None and self.callback:
            self.callback(plan)
        return GLib.SOURCE_REMOVE

    def close(self):
        self.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
