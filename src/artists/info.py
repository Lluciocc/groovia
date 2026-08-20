# info.py
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

import hashlib
import itertools
import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import asdict, replace
from pathlib import Path
from typing import Callable

from ..library.grouping import normalize_group_name
from ..logging_utils import configure_logger
from .models import ArtistMetadata
from .theaudiodb import ArtistLookup, TheAudioDBError, TheAudioDBRateLimited

LOGGER = logging.getLogger("groovia.artist-info")
configure_logger(LOGGER, "Groovia artist info")

THEAUDIODB_FREE_LIMIT = 30
THEAUDIODB_REQUEST_BUDGET = 28
THEAUDIODB_WINDOW_SECONDS = 60.0
POSITIVE_TTL = 60 * 24 * 60 * 60
NEGATIVE_TTL = 7 * 24 * 60 * 60
TRANSIENT_FAILURE_TTL = 5 * 60
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_429_RETRIES = 1

PRIORITIES = {"high": 0, "normal": 10, "low": 20}


class SlidingWindowRateLimiter:
    """Strict rolling-window limiter with an injectable clock and waiter."""

    def __init__(
        self,
        budget: int = THEAUDIODB_REQUEST_BUDGET,
        window_seconds: float = THEAUDIODB_WINDOW_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
        waiter: Callable[[float], object] | None = None,
    ):
        self.budget = max(1, int(budget))
        self.window_seconds = max(0.001, float(window_seconds))
        self.clock = clock
        self.waiter = waiter or (lambda seconds: threading.Event().wait(seconds))
        self.request_times: deque[float] = deque()
        self.blocked_until = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        logged = False
        while True:
            with self._lock:
                now = self.clock()
                cutoff = now - self.window_seconds
                while self.request_times and self.request_times[0] <= cutoff:
                    self.request_times.popleft()
                if self.blocked_until > now:
                    delay = self.blocked_until - now
                elif len(self.request_times) >= self.budget:
                    delay = self.request_times[0] + self.window_seconds - now
                else:
                    self.request_times.append(now)
                    return True
            if not logged:
                LOGGER.info("TheAudioDB rate limit reached; delaying queued lookups")
                logged = True
            if self.waiter(max(0.001, delay)):
                return False

    def block_for(self, seconds: float) -> float:
        with self._lock:
            self.blocked_until = max(
                self.blocked_until,
                self.clock() + max(1.0, float(seconds)),
            )
            return self.blocked_until


class ArtistInfoService:
    def __init__(
        self,
        cache_dir: str | Path,
        provider,
        *,
        limiter: SlidingWindowRateLimiter | None = None,
        dispatcher: Callable[[Callable, object], object] | None = None,
        activity_callback: Callable[[int], object] | None = None,
        downloader: Callable[[str, Path], Path | None] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir = self.cache_dir / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_path = self.cache_dir / "artists.json"
        self.provider = provider
        self.dispatcher = dispatcher or (lambda callback, result: callback(result))
        self.activity_callback = activity_callback
        self.downloader = downloader or self.download_image
        self.clock = clock
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.rate_limiter = limiter or SlidingWindowRateLimiter(waiter=self._stop.wait)
        self._records = self._load_records()
        self._in_flight: dict[str, list[Callable]] = {}
        self._transient_failures: dict[str, float] = {}
        self._jobs: queue.PriorityQueue = queue.PriorityQueue()
        self._sequence = itertools.count()
        self._generation = 0
        self._closed = False
        self._worker = threading.Thread(
            target=self._run_jobs,
            name="groovia-artist-info",
            daemon=True,
        )
        self._worker.start()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._in_flight.clear()
        self._stop.set()
        self._jobs.put((-1, next(self._sequence), self._generation, None, None))

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._in_flight)

    def cancel_pending(self) -> int:
        """Cancel queued callbacks and prevent queued jobs from reaching the provider.

        A request already being handled by urllib cannot be interrupted safely, but
        its result is ignored by the UI and no other job from that generation runs.
        """
        with self._lock:
            if self._closed:
                return 0
            cancelled = len(self._in_flight)
            self._generation += 1
            self._in_flight.clear()
        if cancelled:
            LOGGER.info("Cancelled queued artist metadata lookups count=%d", cancelled)
            self._notify_activity()
        return cancelled

    def get_cached(self, artist_name: str) -> ArtistMetadata | None:
        record = self.cached_record(artist_name)
        if record and (not record.not_found or record.artwork_available):
            LOGGER.debug("Artist metadata cache hit artist=%r", artist_name)
            return record
        return None

    def cached_record(self, artist_name: str) -> ArtistMetadata | None:
        key = normalize_group_name(artist_name)
        with self._lock:
            record = self._records.get(key)
            return ArtistMetadata(**asdict(record)) if record else None

    def resolve_async(self, artist_name: str, callback=None, *, priority="normal") -> bool:
        key = normalize_group_name(artist_name)
        if not key:
            if callback:
                self._dispatch(callback, None)
            return False
        record = self.cached_record(artist_name)
        if record and self._is_fresh(record):
            if callback:
                self._dispatch(callback, self._visible_record(record))
            return False
        with self._lock:
            if self._closed:
                if callback:
                    self._dispatch(callback, self._visible_record(record))
                return False
            if self._transient_failures.get(key, 0.0) > self.clock():
                if callback:
                    self._dispatch(callback, self._visible_record(record))
                return False
            callbacks = self._in_flight.get(key)
            if callbacks is not None:
                if callback:
                    callbacks.append(callback)
                return False
            self._in_flight[key] = [callback] if callback else []
            generation = self._generation
        queue_priority = PRIORITIES.get(priority, priority if isinstance(priority, int) else 10)
        self._jobs.put((queue_priority, next(self._sequence), generation, artist_name, key))
        LOGGER.info("TheAudioDB lookup queued artist=%r", artist_name)
        self._notify_activity()
        return True

    def resolve(self, artist_name: str) -> ArtistMetadata | None:
        key = normalize_group_name(artist_name)
        if not key:
            return None
        existing = self.cached_record(artist_name)
        if existing and self._is_fresh(existing):
            return self._visible_record(existing)
        for attempt in range(MAX_429_RETRIES + 1):
            if not self.rate_limiter.acquire():
                return self._visible_record(existing)
            try:
                LOGGER.info("TheAudioDB lookup started artist=%r", artist_name)
                lookup: ArtistLookup = self.provider.resolve_artist(artist_name)
                break
            except TheAudioDBRateLimited as error:
                blocked_until = self.rate_limiter.block_for(error.retry_after)
                LOGGER.warning("TheAudioDB provider blocked by 429 until=%.3f", blocked_until)
                if attempt >= MAX_429_RETRIES:
                    self._mark_transient_failure(key)
                    return self._visible_record(existing)
            except (TheAudioDBError, OSError, ValueError) as error:
                LOGGER.warning("TheAudioDB request failed artist=%r error=%s", artist_name, error)
                self._mark_transient_failure(key)
                return self._visible_record(existing)
            except Exception:
                LOGGER.exception("Unexpected artist provider failure artist=%r", artist_name)
                self._mark_transient_failure(key)
                return self._visible_record(existing)
        else:
            return self._visible_record(existing)

        if lookup.metadata is None:
            LOGGER.info("TheAudioDB artist not found artist=%r", artist_name)
            negative = self._negative_record(artist_name, key, existing, lookup.raw_json)
            self._store_record(negative)
            return self._visible_record(negative)

        metadata = lookup.metadata
        metadata.last_checked = self.clock()
        metadata.not_found = False
        metadata.raw_json = lookup.raw_json or metadata.raw_json
        if metadata.image_url:
            destination = self.images_dir / self._image_stem(metadata, key)
            try:
                image_path = self.downloader(self.medium_image_url(metadata.image_url), destination)
            except Exception:
                LOGGER.exception(
                    "Unexpected artist artwork download failure artist=%r", artist_name
                )
                image_path = None
            if image_path:
                metadata.image_path = str(image_path)
                LOGGER.info("Artist artwork downloaded artist=%r", artist_name)
            elif existing and existing.artwork_available:
                metadata.image_path = existing.image_path
        elif existing and existing.artwork_available:
            metadata.image_path = existing.image_path
        self._store_record(metadata)
        LOGGER.info("TheAudioDB artist matched artist=%r", artist_name)
        LOGGER.info("TheAudioDB metadata cached artist=%r", artist_name)
        return metadata

    def _run_jobs(self):
        while not self._stop.is_set():
            _priority, _sequence, generation, artist_name, key = self._jobs.get()
            if artist_name is None or key is None:
                return
            with self._lock:
                if generation != self._generation:
                    continue
            try:
                result = self.resolve(artist_name)
            except Exception:
                # Keep the shared worker alive if a future provider or cache
                # implementation raises an error we did not anticipate.
                LOGGER.exception("Unexpected artist metadata worker failure artist=%r", artist_name)
                result = self.get_cached(artist_name)
            finally:
                with self._lock:
                    current_generation = generation == self._generation
                    callbacks = self._in_flight.pop(key, []) if current_generation else []
                    closed = self._closed
            if current_generation:
                self._notify_activity()
            if not closed:
                for callback in callbacks:
                    self._dispatch(callback, result)

    def _notify_activity(self):
        if self.activity_callback is not None:
            self._dispatch(self.activity_callback, self.pending_count)

    def _dispatch(self, callback, result):
        try:
            self.dispatcher(callback, result)
        except Exception:
            LOGGER.warning("Artist metadata callback failed", exc_info=True)

    def _mark_transient_failure(self, key: str):
        with self._lock:
            self._transient_failures[key] = self.clock() + TRANSIENT_FAILURE_TTL

    def _is_fresh(self, record: ArtistMetadata) -> bool:
        ttl = NEGATIVE_TTL if record.not_found else POSITIVE_TTL
        fresh = record.last_checked >= self.clock() - ttl
        if not fresh:
            LOGGER.debug("Artist metadata stale artist=%r", record.display_name)
        return fresh

    @staticmethod
    def _visible_record(record: ArtistMetadata | None) -> ArtistMetadata | None:
        if record is None:
            return None
        return record if not record.not_found or record.artwork_available else None

    def _negative_record(self, artist_name, key, existing, raw_json):
        if existing:
            return replace(
                existing,
                normalized_name=key,
                not_found=True,
                raw_json=raw_json,
                last_checked=self.clock(),
            )
        return ArtistMetadata(
            normalized_name=key,
            display_name=artist_name,
            provider="theaudiodb",
            raw_json=raw_json,
            not_found=True,
            last_checked=self.clock(),
        )

    def _load_records(self) -> dict[str, ArtistMetadata]:
        try:
            payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        values = payload.get("artists", {}) if payload.get("version") else payload
        if not isinstance(values, dict):
            return {}
        records = {}
        fields = ArtistMetadata.__dataclass_fields__
        for cache_key, value in values.items():
            if not isinstance(value, dict):
                continue
            migrated = self._migrate_record(cache_key, value)
            cleaned = {key: item for key, item in migrated.items() if key in fields}
            try:
                record = ArtistMetadata(**cleaned)
                record.last_checked = max(0.0, float(record.last_checked or 0.0))
                record.image_path = self._cached_image_path(record.image_path)
            except (TypeError, ValueError):
                continue
            records[record.normalized_name] = record
        return records

    def _migrate_record(self, cache_key: str, value: dict) -> dict:
        normalized_name = normalize_group_name(value.get("normalized_name") or cache_key)
        raw_json = value.get("raw_json")
        if isinstance(raw_json, dict):
            raw_json = json.dumps(raw_json, ensure_ascii=False, separators=(",", ":"))
        migrated = dict(value)
        migrated.update(
            normalized_name=normalized_name,
            display_name=str(value.get("display_name") or cache_key),
            raw_json=raw_json if isinstance(raw_json, str) else None,
        )
        if "spotify_artist_id" in value or value.get("provider") == "spotify":
            migrated.update(
                provider="spotify",
                provider_artist_id=value.get("provider_artist_id")
                or value.get("spotify_artist_id"),
                provider_url=value.get("provider_url") or value.get("spotify_url"),
                not_found=bool(value.get("not_found", False)),
            )
        return migrated

    def _store_record(self, record: ArtistMetadata):
        with self._lock:
            if self._closed:
                return
            self._records[record.normalized_name] = record
            payload = {
                "version": 2,
                "artists": {key: asdict(value) for key, value in self._records.items()},
            }
            temporary = self.metadata_path.with_suffix(".tmp")
            try:
                temporary.write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                temporary.replace(self.metadata_path)
            except OSError:
                LOGGER.warning("Could not persist artist metadata cache", exc_info=True)
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _cached_image_path(self, value) -> str | None:
        if not value:
            return None
        try:
            path = Path(str(value)).resolve()
            if path.is_relative_to(self.images_dir.resolve()) and path.is_file():
                return str(path)
        except (OSError, TypeError, ValueError):
            pass
        return None

    @staticmethod
    def _image_stem(metadata: ArtistMetadata, key: str) -> str:
        provider_id = metadata.provider_artist_id or hashlib.sha256(key.encode()).hexdigest()
        safe_id = (
            provider_id
            if provider_id.isalnum()
            else hashlib.sha256(provider_id.encode()).hexdigest()
        )
        return f"theaudiodb-{safe_id[:80]}"

    @staticmethod
    def medium_image_url(image_url: str) -> str:
        parsed = urllib.parse.urlparse(image_url)
        if parsed.path.endswith(("/medium", "/small")):
            return image_url
        return urllib.parse.urlunparse(parsed._replace(path=f"{parsed.path.rstrip('/')}/medium"))

    @staticmethod
    def download_image(image_url: str, destination: Path) -> Path | None:
        try:
            parsed = urllib.parse.urlparse(image_url)
            hostname = (parsed.hostname or "").casefold()
        except ValueError:
            return None
        if parsed.scheme != "https" or not (
            hostname == "theaudiodb.com" or hostname.endswith(".theaudiodb.com")
        ):
            LOGGER.warning("Artist artwork download rejected host=%r", hostname)
            return None
        request = urllib.request.Request(
            image_url,
            headers={"Accept": "image/*", "User-Agent": "Groovia/artist-info"},
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                data = response.read(MAX_IMAGE_BYTES + 1)
            if not data or len(data) > MAX_IMAGE_BYTES:
                return None
            signatures = (
                (".webp", data.startswith(b"RIFF") and data[8:12] == b"WEBP"),
                (".png", data.startswith(bytes([137]) + b"PNG")),
                (".jpg", data.startswith(bytes([255, 216]))),
            )
            suffix = next((suffix for suffix, matches in signatures if matches), None)
            if suffix is None:
                return None
            final_path = destination.with_suffix(suffix)
            temporary = final_path.with_suffix(f"{suffix}.tmp")
            temporary.write_bytes(data)
            temporary.replace(final_path)
            return final_path
        except (OSError, ValueError, urllib.error.URLError):
            LOGGER.warning("Artist artwork download failed", exc_info=True)
            return None
