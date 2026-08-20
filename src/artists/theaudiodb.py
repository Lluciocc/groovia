# theaudiodb.py
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

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable

from ..library.grouping import normalize_group_name
from .models import ArtistMetadata

THEAUDIODB_API_KEY = "123"
THEAUDIODB_API_BASE = f"https://www.theaudiodb.com/api/v1/json/{THEAUDIODB_API_KEY}"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class TheAudioDBError(RuntimeError):
    pass


class TheAudioDBRateLimited(TheAudioDBError):
    def __init__(self, retry_after: float = 60.0):
        super().__init__(f"TheAudioDB rate limited for {retry_after:.0f} seconds")
        self.retry_after = max(1.0, float(retry_after))


@dataclass(slots=True)
class ArtistLookup:
    metadata: ArtistMetadata | None
    raw_json: str


def normalize_artist_name(value: str | None) -> str:
    return normalize_group_name(value)


def find_exact_artist_match(query_name: str, artists: Iterable[dict]) -> dict | None:
    wanted = normalize_artist_name(query_name)
    if not wanted:
        return None
    return next(
        (
            artist
            for artist in artists or ()
            if isinstance(artist, dict) and normalize_artist_name(artist.get("strArtist")) == wanted
        ),
        None,
    )


def _text(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _long_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class TheAudioDBArtistProvider:
    def __init__(
        self,
        *,
        base_url: str = THEAUDIODB_API_BASE,
        timeout: float = 8.0,
        version: str = "dev",
        opener: Callable | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = max(1.0, float(timeout))
        self.user_agent = f"Groovia/{version or 'dev'} (https://github.com/Lluciocc/groovia)"
        self._opener = opener or urllib.request.urlopen

    def search_artist(self, artist_name: str) -> dict:
        query = urllib.parse.urlencode({"s": artist_name})
        return self._request_json(f"/search.php?{query}")

    def resolve_artist(self, artist_name: str) -> ArtistLookup:
        payload = self.search_artist(artist_name)
        artists = payload.get("artists") if isinstance(payload, dict) else None
        matched = find_exact_artist_match(artist_name, artists if isinstance(artists, list) else ())
        if matched is None:
            return ArtistLookup(
                metadata=None,
                raw_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
        return ArtistLookup(
            metadata=self.normalize_response(matched, artist_name),
            raw_json=json.dumps(matched, ensure_ascii=False, separators=(",", ":")),
        )

    def normalize_response(self, artist_data: dict, query_name: str = "") -> ArtistMetadata:
        display_name = _text(artist_data.get("strArtist")) or _text(query_name) or "Unknown Artist"
        normalized_name = normalize_artist_name(query_name or display_name)
        biography = next(
            (
                value
                for key in (
                    "strBiographyFR",
                    "strBiographyEN",
                    "strBiography",
                    "strBiographyES",
                    "strBiographyDE",
                )
                if (value := _long_text(artist_data.get(key)))
            ),
            None,
        )
        provider_id = _text(artist_data.get("idArtist"))
        return ArtistMetadata(
            normalized_name=normalized_name,
            display_name=display_name,
            provider="theaudiodb",
            provider_artist_id=provider_id,
            provider_url=(
                f"https://www.theaudiodb.com/artist/{provider_id}" if provider_id else None
            ),
            alternate_name=_text(artist_data.get("strArtistAlternate")),
            biography=biography,
            country=_text(artist_data.get("strCountry")),
            country_code=_text(artist_data.get("strCountryCode")),
            formed_year=_text(artist_data.get("intFormedYear")),
            born_year=_text(artist_data.get("intBornYear")),
            died_year=_text(artist_data.get("intDiedYear")),
            disbanded=_text(artist_data.get("strDisbanded")),
            genre=_text(artist_data.get("strGenre")),
            style=_text(artist_data.get("strStyle")),
            mood=_text(artist_data.get("strMood")),
            label=_text(artist_data.get("strLabel")),
            website=_text(artist_data.get("strWebsite")),
            facebook=_text(artist_data.get("strFacebook")),
            twitter=_text(artist_data.get("strTwitter")),
            image_url=_text(artist_data.get("strArtistThumb")),
            wide_thumb_url=_text(artist_data.get("strArtistWideThumb")),
            fanart_url=_text(artist_data.get("strArtistFanart")),
            fanart2_url=_text(artist_data.get("strArtistFanart2")),
            fanart3_url=_text(artist_data.get("strArtistFanart3")),
            fanart4_url=_text(artist_data.get("strArtistFanart4")),
            banner_url=_text(artist_data.get("strArtistBanner")),
            logo_url=_text(artist_data.get("strArtistLogo")),
            cutout_url=_text(artist_data.get("strArtistCutout")),
            clearart_url=_text(artist_data.get("strArtistClearart")),
            raw_json=json.dumps(artist_data, ensure_ascii=False, separators=(",", ":")),
        )

    def _request_json(self, endpoint: str) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{endpoint}",
            headers={"Accept": "application/json", "User-Agent": self.user_agent},
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise TheAudioDBError("TheAudioDB response exceeded the size limit")
            payload = json.loads(data.decode("utf-8"))
            if not isinstance(payload, dict):
                raise TheAudioDBError("TheAudioDB returned an invalid response")
            return payload
        except urllib.error.HTTPError as error:
            if error.code == 429:
                raise TheAudioDBRateLimited(self._retry_after(error)) from error
            raise TheAudioDBError(f"TheAudioDB returned HTTP {error.code}") from error
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise TheAudioDBError("TheAudioDB request failed") from error

    @staticmethod
    def _retry_after(error: urllib.error.HTTPError) -> float:
        try:
            return max(1.0, float(error.headers.get("Retry-After", "60")))
        except (AttributeError, TypeError, ValueError):
            return 60.0
