# lrclib.py
#
# Copyright 2026 Lluciocc (llucio.cc00@gmail.com)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Small, dependency-free client for the public LRCLIB lyrics API."""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://lrclib.net/api/"
USER_AGENT = "Groovia/1.0 (lyrics lookup)"


@dataclass(slots=True)
class LyricsResult:
    id: int | None
    synced_lyrics: str | None = None
    plain_lyrics: str | None = None


def _normalize(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", value.lower())
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


class LrcLibClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 12.0):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout

    def _get(self, endpoint: str, params: dict[str, str | int]) -> object | None:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{self.base_url}{endpoint}?{query}",
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _matches(item: dict, title: str, artist: str, duration: float) -> bool:
        item_title = _normalize(item.get("trackName") or item.get("track_name"))
        item_artist = _normalize(item.get("artistName") or item.get("artist_name"))
        wanted_title = _normalize(title)
        wanted_artist = _normalize(artist)
        if not item_title or not item_artist:
            return False
        title_matches = (
            item_title == wanted_title or item_title in wanted_title or wanted_title in item_title
        )
        # Artist names are a stronger identity signal than titles: substring
        # matching would incorrectly accept "Other Artist" for "Artist".
        artist_matches = item_artist == wanted_artist
        if not (title_matches and artist_matches):
            return False
        item_duration = item.get("duration") or 0
        return not duration or not item_duration or abs(float(item_duration) - duration) <= 15

    @staticmethod
    def _result(item: object) -> LyricsResult | None:
        if not isinstance(item, dict):
            return None
        synced = item.get("syncedLyrics") or item.get("synced_lyrics")
        plain = item.get("plainLyrics") or item.get("plain_lyrics")
        if not (isinstance(synced, str) and synced.strip()) and not (
            isinstance(plain, str) and plain.strip()
        ):
            return None
        return LyricsResult(
            id=item.get("id"),
            synced_lyrics=synced if isinstance(synced, str) and synced.strip() else None,
            plain_lyrics=plain if isinstance(plain, str) and plain.strip() else None,
        )

    def get_lyrics(
        self, title: str, artist: str, album: str = "", duration: float = 0
    ) -> LyricsResult | None:
        if not title or not artist:
            return None
        params: dict[str, str | int] = {
            "track_name": title,
            "artist_name": artist,
        }
        if album:
            params["album_name"] = album
        if duration:
            params["duration"] = round(duration)

        exact = self._get("get", params)
        if isinstance(exact, dict) and self._matches(exact, title, artist, duration):
            result = self._result(exact)
            if result:
                return result

        search = self._get("search", {"track_name": title, "artist_name": artist})
        items = search.get("data", []) if isinstance(search, dict) else search
        if not isinstance(items, list):
            return None
        for item in items:
            if self._matches(item, title, artist, duration):
                result = self._result(item)
                if result:
                    return result
        return None
