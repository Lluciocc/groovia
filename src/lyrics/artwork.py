# artwork.py
#
# Copyright 2026 Lluciocc (llucio.cc00@gmail.com)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Asynchronous-friendly cache for optional Better Lyrics artwork."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .better_lyrics import DEFAULT_BASE_URL, USER_AGENT


class BetterLyricsArtworkClient:
    def __init__(
        self, cache_dir: str | Path, base_url: str = DEFAULT_BASE_URL, timeout: float = 15.0
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._fetch_lock = threading.Lock()
        self.negative_cache_ttl = 24 * 60 * 60

    @staticmethod
    def _key(title: str, artist: str, album: str) -> str:
        value = "\x1f".join(item.strip().casefold() for item in (title, artist, album))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _paths(self, key: str) -> tuple[Path, Path]:
        return self.cache_dir / f"{key}.art", self.cache_dir / f"{key}.json"

    def cached(self, title: str, artist: str, album: str = "") -> Path | None:
        path, _metadata = self._paths(self._key(title, artist, album))
        candidates = [
            path.with_suffix(suffix) for suffix in (".gif", ".webp", ".png", ".jpg", ".jpeg")
        ]
        return next((item for item in candidates if item.is_file() and item.stat().st_size), None)

    def unavailable_cached(self, title: str, artist: str, album: str = "") -> bool:
        """Return whether a recent artwork miss should suppress another request."""
        _art_path, metadata_path = self._paths(self._key(title, artist, album))
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            attempted_at = float(metadata.get("attempted_at", 0))
        except (OSError, TypeError, ValueError, UnicodeError):
            return False
        return (
            metadata.get("available") is False
            and attempted_at > time.time() - self.negative_cache_ttl
        )

    def _mark_unavailable(self, metadata_path: Path) -> None:
        try:
            metadata_path.write_text(
                json.dumps({"available": False, "attempted_at": time.time()}),
                encoding="utf-8",
            )
        except OSError:
            pass

    def fetch(self, title: str, artist: str, album: str = "") -> Path | None:
        if not title or not artist:
            return None
        # The artwork service is shared by automatic enrichment and the Lyrics
        # page.  Serializing this small cache operation prevents those two
        # workers from downloading the same artwork concurrently.
        with self._fetch_lock:
            key = self._key(title, artist, album)
            destination, metadata_path = self._paths(key)
            cached = self.cached(title, artist, album)
            if cached:
                return cached
            if self.unavailable_cached(title, artist, album):
                return None
            query = urllib.parse.urlencode({"s": title, "a": artist})
            request = urllib.request.Request(
                f"{self.base_url}/artwork?{query}",
                headers={"Accept": "application/json, image/*", "User-Agent": USER_AGENT},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    content_type = (response.headers.get_content_type() or "").lower()
                    data = response.read()
                    if not data:
                        self._mark_unavailable(metadata_path)
                        return None
                    signatures = (
                        ("image/gif", data.startswith(b"GIF")),
                        ("image/webp", data.startswith(b"RIFF") and data[8:12] == b"WEBP"),
                        ("image/png", data.startswith(bytes([137]) + b"PNG")),
                        ("image/jpeg", data.startswith(bytes([255, 216]))),
                    )
                    detected_type = next((kind for kind, matches in signatures if matches), None)
                    if detected_type is None:
                        self._mark_unavailable(metadata_path)
                        return None
                    content_type = detected_type
                    suffix = (
                        ".gif"
                        if "gif" in content_type
                        else ".webp"
                        if "webp" in content_type
                        else ".png"
                    )
                    if "jpeg" in content_type:
                        suffix = ".jpg"
                    destination = destination.with_suffix(suffix)
                    destination.write_bytes(data)
                    metadata_path.write_text(
                        json.dumps(
                            {
                                "available": True,
                                "content_type": content_type,
                                "animated": suffix == ".gif",
                            }
                        ),
                        encoding="utf-8",
                    )
                    return destination
            except (OSError, urllib.error.HTTPError, ValueError, UnicodeError):
                self._mark_unavailable(metadata_path)
                return None
