"""Asynchronous Auto DJ analysis and planning service."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from gi.repository import GLib

from .analysis import AnalysisCache, TrackAnalyzer
from .planner import TransitionPlanner


class AutoDJService:
    def __init__(self, callback=None, data_dir=None):
        self.callback = callback
        self.analyzer = TrackAnalyzer(AnalysisCache(data_dir))
        self.planner = TransitionPlanner()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="groovia-autodj")
        self._lock = threading.Lock()
        self._generation = 0

    def prepare(self, current, following, options=None):
        self.cancel()
        if not current or not following or current.path == following.path:
            return
        with self._lock:
            generation = self._generation
        options = dict(options or {})

        def worker():
            try:
                left = self.analyzer.analyze(current)
                right = self.analyzer.analyze(following)
                plan = self.planner.plan(current, following, left, right, options)
            except Exception:
                # A plan is optional; playback must remain available even if a
                # decoder or an analyzer fails.
                plan = None
            GLib.idle_add(self._deliver, generation, plan)

        self._executor.submit(worker)

    def cancel(self):
        with self._lock:
            self._generation += 1

    def _deliver(self, generation, plan):
        with self._lock:
            current_generation = self._generation
        if generation == current_generation and plan is not None and self.callback:
            self.callback(plan)
        return GLib.SOURCE_REMOVE

    def close(self):
        self.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
