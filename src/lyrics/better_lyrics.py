# better_lyrics.py
#
# Copyright 2026 Lluciocc (llucio.cc00@gmail.com)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Dependency-free Better Lyrics API client."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://lyrics-api.boidu.dev"
USER_AGENT = "Groovia/1.1.1 (Better Lyrics client)"


@dataclass(slots=True)
class BetterLyricsResult:
    ttml: str
    score: float | None = None
    provider: str | None = None
    cache_status: str | None = None
    auth_mode: str | None = None
    rate_limit_status: dict[str, str] = field(default_factory=dict)


class BetterLyricsClient:
    """Fetch raw TTML without ever making provider failures fatal to callers."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 12.0,
        api_key: str | None = None,
        user_agent: str = USER_AGENT,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key or os.environ.get("GROOVIA_BETTER_LYRICS_API_KEY")
        self.user_agent = user_agent
        self.last_status: int | None = None
        self.last_error: str | None = None

    def _request(
        self, endpoint: str, params: dict[str, str | int | float]
    ) -> BetterLyricsResult | None:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{self.base_url}/{endpoint.lstrip('/')}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": self.user_agent,
                **({"X-API-Key": self.api_key} if self.api_key else {}),
            },
        )
        self.last_status = None
        self.last_error = None
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self.last_status = int(response.status)
                payload = json.loads(response.read().decode("utf-8"))
                headers = response.headers
        except urllib.error.HTTPError as error:
            self.last_status = int(error.code)
            self.last_error = str(error)
            return None
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            self.last_error = str(error)
            return None
        if not isinstance(payload, dict):
            self.last_error = "Better Lyrics returned a non-object JSON response"
            return None
        ttml = payload.get("ttml")
        if not isinstance(ttml, str) or not ttml.strip():
            self.last_error = "Better Lyrics response did not contain TTML"
            return None
        rate_limit_status = {
            key.removeprefix("X-").lower().replace("-", "_"): value
            for key, value in headers.items()
            if key.lower().startswith("x-ratelimit-")
        }
        score = payload.get("score")
        try:
            score = float(score) if score is not None else None
        except (TypeError, ValueError):
            score = None
        return BetterLyricsResult(
            ttml=ttml,
            score=score,
            provider=headers.get("X-Provider") or "ttml",
            cache_status=headers.get("X-Cache-Status"),
            auth_mode=headers.get("X-Auth-Mode"),
            rate_limit_status=rate_limit_status,
        )

    def get_lyrics(
        self, title: str, artist: str, album: str = "", duration: float = 0
    ) -> BetterLyricsResult | None:
        if not title or not artist:
            return None
        params: dict[str, str | int | float] = {"s": title, "a": artist}
        if album:
            params["al"] = album
        if duration:
            params["d"] = round(duration)
        return self._request("getLyrics", params)
