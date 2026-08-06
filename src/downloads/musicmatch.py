"""Token-based Musixmatch client for word-by-word (richsync) synced lyrics.

Reference implementation:
https://github.com/ybenyedder/musixmatch-freeAPI/blob/main/python/musixmatch_richsync/client.py

Zero dependencies (standard library only).

Flow:
  1. token.get            -> an anonymous user_token (rate-limited; cached + persisted).
  2. macro.subtitles.get  -> matches (title, artist, album, duration) and returns the
                             matched track + its line-level subtitle (LRC).
  3. track.richsync.get   -> per-word timing for tracks that have richsync.

A user_token has a limited call quota; on HTTP status 401 the client transparently
fetches a fresh token (with backoff) and retries once.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_BASE = "https://apic-desktop.musixmatch.com/ws/1.1/"
APP_ID = "web-desktop-app-v1.0"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class MusixmatchError(Exception):
    """Raised for unrecoverable client errors (never on a simple no-match)."""


@dataclass
class LyricsResult:
    """A lyrics match. ``kind`` is ``"word"`` (richsync) or ``"line"`` (subtitle)."""

    lrc: str
    kind: str
    track_name: str
    artist_name: str
    commontrack_id: int

    @property
    def is_word_synced(self) -> bool:
        return self.kind == "word"


# ── helpers ──────────────────────────────────────────────────────────────────
def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\(feat\.[^)]*\)|\[[^\]]*\]", " ", s.lower())
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def _deep_find(obj, key):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                return v
            r = _deep_find(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _deep_find(v, key)
            if r is not None:
                return r
    return None


def _stamp(t: float) -> str:
    t = max(0.0, t)
    m, s = int(t // 60), int(t % 60)
    c = round((t - int(t)) * 100)
    if c >= 100:
        c -= 100
        s += 1
    if s >= 60:
        s -= 60
        m += 1
    return f"{m:02d}:{s:02d}.{c:02d}"


def richsync_to_lrc(richsync_body: str) -> str:
    """Convert a ``richsync_body`` JSON string into enhanced (word-timed) LRC.

    Returns ``""`` when the body carries no per-word timing.
    """
    try:
        entries = json.loads(richsync_body)
    except (json.JSONDecodeError, TypeError):
        return ""
    out, has_words = [], False
    for e in entries:
        ts = e.get("ts")
        if ts is None:
            continue
        words = [(ts + (w.get("o") or 0), str(w.get("c") or "").strip()) for w in (e.get("l") or [])]
        words = [(wt, wc) for wt, wc in words if wc]
        if words:
            has_words = True
            out.append((ts, f"[{_stamp(ts)}]" + " ".join(f"<{_stamp(wt)}>{wc}" for wt, wc in words)))
        elif (e.get("x") or "").strip():
            out.append((ts, f"[{_stamp(ts)}]" + e["x"].strip()))
    if not has_words:
        return ""
    out.sort(key=lambda x: x[0])
    return "\n".join(line for _, line in out)


# ── client ───────────────────────────────────────────────────────────────────
class MusixmatchRichsync:
    """Minimal Musixmatch client focused on synced (word + line) lyrics.

    :param base_url: API host override (default apic-desktop).
    :param timeout: per-request timeout in seconds.
    :param token_cache: optional path to persist the user token across runs.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE,
        timeout: float = 15.0,
        token_cache: Optional[Path] = None,
    ):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.token_cache = Path(token_cache) if token_cache else (Path.home() / ".cache" / "musixmatch-richsync-token.json")
        self._token = None
        self._token_at = 0.0

    # -- low-level request -----------------------------------------------------
    def _get(self, endpoint: str, params: dict):
        query = urllib.parse.urlencode({"app_id": APP_ID, "format": "json", **params})
        req = urllib.request.Request(
            f"{self.base_url}{endpoint}?{query}",
            headers={"User-Agent": USER_AGENT, "Cookie": "x-mxm-token-guid="},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                text = r.read().decode("utf-8", "replace")
            if text.lstrip().startswith("<"):  # captcha / HTML challenge
                return None
            return json.loads(text)
        except Exception:
            return None

    # -- token -----------------------------------------------------------------
    def _load_persisted(self):
        if self._token:
            return
        try:
            d = json.loads(self.token_cache.read_text("utf-8"))
            self._token, self._token_at = d["v"], d["t"]
        except Exception:
            pass

    def get_token(self, force: bool = False, ttl: float = 6 * 3600) -> Optional[str]:
        """Return a usable user token, fetching+persisting one when needed.

        token.get is IP rate-limited, so this retries with backoff and caches the
        result (in memory and on disk) for ``ttl`` seconds.
        """
        self._load_persisted()
        if not force and self._token and time.time() - self._token_at < ttl:
            return self._token
        for delay in (0, 20, 45, 90):
            if delay:
                time.sleep(delay)
            d = self._get("token.get", {"t": str(int(time.time() * 1000))})
            tok = (((d or {}).get("message") or {}).get("body") or {}).get("user_token")
            if tok and not tok.startswith("UpgradeOnly"):
                self._token, self._token_at = tok, time.time()
                try:
                    self.token_cache.parent.mkdir(parents=True, exist_ok=True)
                    self.token_cache.write_text(json.dumps({"v": tok, "t": self._token_at}), "utf-8")
                except Exception:
                    pass
                return tok
        return self._token  # fall back to a stale token if we still have one

    # -- matching --------------------------------------------------------------
    @staticmethod
    def _trusted(t: dict, title: str, artist: str, duration: float) -> bool:
        wt, wa = _normalize(title), _normalize(artist)
        gt, ga = _normalize(t.get("track_name", "")), _normalize(t.get("artist_name", ""))
        tl = t.get("track_length") or 0
        dd = abs(tl - duration) if (tl and duration) else 9999
        if dd > 15 and dd != 9999:
            return False
        title_ok = bool(gt) and (gt == wt or wt in gt or gt in wt)
        artist_ok = bool(ga) and (ga == wa or wa in ga or ga in wa)
        return (title_ok and artist_ok) or (dd <= 4 and (title_ok or artist_ok))

    def _macro(self, title, artist, album, duration, token):
        return self._get(
            "macro.subtitles.get",
            {
                "namespace": "lyrics_richsynched",
                "subtitle_format": "lrc",
                "q_track": title,
                "q_artist": artist,
                "q_album": album or "",
                "q_duration": int(duration) if duration else "",
                "usertoken": token,
            },
        )

    # -- public API ------------------------------------------------------------
    def get_richsync(self, commontrack_id: int) -> str:
        """Return enhanced (word-timed) LRC for a commontrack_id, or ``""``."""
        token = self.get_token()
        if not token:
            return ""
        rich = self._get("track.richsync.get", {"commontrack_id": commontrack_id, "usertoken": token})
        body = _deep_find(rich, "richsync_body")
        return richsync_to_lrc(body) if isinstance(body, str) and body else ""

    def get_lyrics(
        self,
        title: str,
        artist: str,
        album: str = "",
        duration: float = 0,
        words_only: bool = False,
    ) -> Optional[LyricsResult]:
        """Match a track and return only Musixmatch richsync lyrics."""
        if not title or not artist:
            return None
        token = self.get_token()
        if not token:
            return None
        macro = self._macro(title, artist, album, duration, token)
        if _deep_find(macro, "status_code") == 401:  # token exhausted -> refresh once
            token = self.get_token(force=True)
            if not token:
                return None
            macro = self._macro(title, artist, album, duration, token)
        if not macro:
            return None

        track = _deep_find(_deep_find(macro, "matcher.track.get"), "track") or {}
        if not track or not self._trusted(track, title, artist, duration) or track.get("instrumental"):
            return None
        ctid = track.get("commontrack_id")
        meta = dict(track_name=track.get("track_name", ""), artist_name=track.get("artist_name", ""), commontrack_id=ctid or 0)

        if track.get("has_richsync") and ctid:
            lrc = self.get_richsync(ctid)
            if lrc:
                return LyricsResult(lrc=lrc, kind="word", **meta)
        return None
