# service.py
#
# Copyright 2026 Lluciocc (llucio.cc00@gmail.com)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Lyrics provider orchestration, persistence, and local fallback handling."""

from __future__ import annotations

import hashlib
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from ..platform_compat import get_data_dir
from .artwork import BetterLyricsArtworkClient
from .better_lyrics import BetterLyricsClient
from .lrclib import LrcLibClient
from .parser import LyricsParseError, LyricsTimeline, parse_lyrics


@dataclass(slots=True)
class LyricsBundle:
    """Display variants backed by one or more persisted source documents."""

    word: LyricsTimeline | None = None
    line: LyricsTimeline | None = None
    plain: LyricsTimeline | None = None

    @property
    def preferred(self) -> LyricsTimeline | None:
        # Data quality and default presentation are separate. LyricsView still
        # intentionally opens in Lines mode for a calmer first view.
        return self.word or self.line or self.plain

    def __bool__(self):
        return bool(self.word or self.line or self.plain)


@dataclass(slots=True)
class LyricsEnrichment:
    """Result delivered after an imported track's optional enrichment."""

    track: object
    bundle: LyricsBundle | None = None
    artwork: Path | None = None
    error: str | None = None


class LyricsService:
    def __init__(self, database, scanner, data_dir: str | Path | None = None):
        self.database = database
        self.scanner = scanner
        self.root = Path(data_dir) / "groovia" / "lyrics" if data_dir else get_data_dir() / "lyrics"
        self.root.mkdir(parents=True, exist_ok=True)
        self.better_lyrics = BetterLyricsClient()
        self.lrclib = LrcLibClient()
        self.artwork = BetterLyricsArtworkClient(self.root / "artwork")
        self._enrichment_lock = threading.Lock()
        self._enrichment_inflight: set[object] = set()
        # A failed automatic lookup should not be repeated on every library
        # scan.  This is intentionally process-local so a user can retry from
        # Find Lyrics after restarting Groovia without a database migration.
        self._enrichment_attempted: set[object] = set()

    @staticmethod
    def _checksum(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _kind(path: Path, timeline: LyricsTimeline) -> str:
        return "synced" if timeline.synchronized else "plain"

    def _read_file(self, path: Path, provider: str | None = None) -> LyricsTimeline | None:
        try:
            content = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            return None
        if not content.strip():
            return None
        try:
            return parse_lyrics(content, file_path=str(path), provider=provider)
        except LyricsParseError:
            return None

    def _save_mapping(
        self,
        track,
        path: Path,
        timeline: LyricsTimeline,
        *,
        provider=None,
        source_id=None,
        user_edited=False,
        replace=False,
    ):
        # Keep raw TTML byte-for-byte instead of normalizing it with utf-8-sig.
        content = path.read_text(encoding="utf-8")
        existing = self.database.lyrics_for_track(track.id)
        # A user edit is never replaced by a provider refresh. New provider
        # rows may coexist and can be removed independently.
        if not replace and any(
            item.get("user_edited") and item.get("file_path") == str(path) for item in existing
        ):
            return
        self.database.save_lyrics(
            track.id,
            self._kind(path, timeline),
            str(path),
            provider or timeline.provider,
            timeline.language,
            content,
            user_edited=user_edited,
            timing_offset_ms=0,
            checksum=self._checksum(content),
            source_id=source_id,
        )

    def ingest_content(
        self,
        track,
        content: str,
        *,
        provider: str,
        source_id: str | None = None,
        replace: bool = False,
        user_edited: bool = False,
        variant: str | None = None,
        metadata: dict | None = None,
    ):
        """Parse and persist a provider document without changing its source."""
        if not content or not content.strip():
            return None
        try:
            timeline = parse_lyrics(content, provider=provider)
        except LyricsParseError:
            return None
        timeline.metadata.update(metadata or {})
        if track.id is None:
            return timeline
        suffix = (
            ".ttml"
            if timeline.source_format == "ttml"
            else ".lrc"
            if timeline.synchronized
            else ".txt"
        )
        safe_provider = (
            "".join(char for char in provider.lower() if char.isalnum() or char in "-_") or "lyrics"
        )
        safe_variant = "".join(
            char for char in (variant or "").lower() if char.isalnum() or char in "-_"
        )
        variant_suffix = f"-{safe_variant}" if safe_variant else ""
        destination = self.root / f"track-{track.id}-{safe_provider}{variant_suffix}{suffix}"
        if not replace:
            existing = self.database.lyrics_for_track(track.id)
            if any(
                item.get("user_edited") and item.get("file_path") == str(destination)
                for item in existing
            ):
                return self._read_file(destination, provider)
        try:
            # write_text receives the exact response. No XML normalization or
            # pretty-printing is performed.
            destination.write_text(content, encoding="utf-8")
            self._save_mapping(
                track,
                destination,
                timeline,
                provider=provider,
                source_id=source_id,
                user_edited=user_edited,
                replace=replace,
            )
        except OSError:
            return None
        loaded = self._read_file(destination, provider)
        if loaded:
            loaded.metadata.update(metadata or {})
        return loaded

    @staticmethod
    def _track_metadata(track) -> tuple[str, str, str, float]:
        return (
            getattr(track, "title", "") or "",
            getattr(track, "artist", "") or "",
            getattr(track, "album", "") or "",
            float(getattr(track, "duration", 0) or 0),
        )

    def fetch_better_lyrics(self, track, *, replace: bool = False) -> LyricsBundle | None:
        title, artist, album, duration = self._track_metadata(track)
        try:
            result = self.better_lyrics.get_lyrics(title, artist, album, duration)
        except Exception:
            return None
        if not result:
            return None
        metadata = {
            "score": result.score,
            "cache_status": result.cache_status,
            "auth_mode": result.auth_mode,
            "rate_limit_status": result.rate_limit_status,
        }
        timeline = self.ingest_content(
            track,
            result.ttml,
            provider="betterlyrics",
            source_id="betterlyrics",
            replace=replace,
            variant="word",
            metadata=metadata,
        )
        if not timeline:
            return None
        return LyricsBundle(word=timeline, line=timeline.line_view())

    def fetch_lrclib(self, track, *, replace: bool = False) -> LyricsBundle | None:
        """Fetch synchronized or plain LRCLIB lyrics as the online fallback."""
        title, artist, album, duration = self._track_metadata(track)
        try:
            result = self.lrclib.get_lyrics(title, artist, album, duration)
        except Exception:
            return None
        if not result:
            return None
        bundle = LyricsBundle()
        source_id = f"lrclib:{result.id}" if result.id is not None else None
        if result.synced_lyrics:
            bundle.line = self.ingest_content(
                track,
                result.synced_lyrics,
                provider="lrclib",
                source_id=source_id,
                replace=replace,
                variant="line",
            )
        if result.plain_lyrics and not bundle.line:
            bundle.plain = self.ingest_content(
                track,
                result.plain_lyrics,
                provider="lrclib",
                source_id=source_id,
                replace=replace,
                variant="plain",
            )
        return bundle if bundle else None

    def fetch_online(self, track, *, replace: bool = False) -> LyricsBundle | None:
        """Fetch the best available online result without accepting plain lyrics early.

        Better Lyrics remains the primary provider.  Its endpoint can however
        return an unsynchronized/plain result when no timed source is
        available.  That result must not prevent LRCLIB from being queried:
        LRCLIB may still have line-synchronized lyrics for the same metadata.
        """
        better = self.fetch_better_lyrics(track, replace=replace)
        if self._bundle_quality(better) >= 1:
            return better

        fallback = self.fetch_lrclib(track, replace=replace)
        if self._bundle_quality(fallback) > self._bundle_quality(better):
            return fallback
        return better or fallback

    @staticmethod
    def _enrichment_key(track):
        """Use the persisted id, with a stable metadata/path fallback for imports."""
        if getattr(track, "id", None) is not None:
            return ("id", track.id)
        return (
            "track",
            getattr(track, "path", "") or "",
            getattr(track, "title", "") or "",
            getattr(track, "artist", "") or "",
            getattr(track, "album", "") or "",
        )

    def _has_cached_lyrics(self, track) -> bool:
        try:
            timeline, row = self.find(track)
            if timeline is None:
                return False
            # A manually edited plain lyric is authoritative and must not be
            # replaced by an automatic provider refresh.  A provider-supplied
            # plain result is different: it is only a cache of text and should
            # not block a later attempt to find synchronized lyrics.
            if timeline.user_edited or (row and row.get("user_edited")):
                return True
            return timeline.synchronized
        except Exception:
            # A malformed embedded tag or an unusual legacy database row must
            # not prevent the online enrichment attempt.
            return False

    def _has_cached_artwork(self, track) -> bool:
        title, artist, album, _duration = self._track_metadata(track)
        if self.artwork.cached(title, artist, album):
            return True
        return bool(
            getattr(self.artwork, "unavailable_cached", lambda *_args: False)(title, artist, album)
        )

    def enrich_track_async(self, track, callback=None) -> bool:
        """Fetch missing Better Lyrics data and artwork in a worker thread.

        This is deliberately a thin layer over ``fetch_online`` and the
        existing artwork client.  It does not create another provider
        pipeline, and all failures are reported as an optional result rather
        than raised to the download/import job.
        """
        if not track:
            return False
        key = self._enrichment_key(track)
        with self._enrichment_lock:
            if key in self._enrichment_attempted or key in self._enrichment_inflight:
                return False
        lyrics_cached = self._has_cached_lyrics(track)
        artwork_cached = self._has_cached_artwork(track)
        if lyrics_cached and artwork_cached:
            return False
        with self._enrichment_lock:
            if key in self._enrichment_attempted or key in self._enrichment_inflight:
                return False
            self._enrichment_inflight.add(key)

        def worker():
            bundle = None
            artwork = None
            errors = []
            try:
                if not lyrics_cached:
                    try:
                        bundle = self.fetch_online(track)
                    except Exception as error:
                        errors.append(f"lyrics: {error}")
                if not artwork_cached:
                    title, artist, album, _duration = self._track_metadata(track)
                    try:
                        artwork = self.artwork.fetch(title, artist, album)
                    except Exception as error:
                        errors.append(f"artwork: {error}")
                result = LyricsEnrichment(
                    track=track,
                    bundle=bundle,
                    artwork=artwork,
                    error="; ".join(errors) if errors else None,
                )
                if callback:
                    callback(result)
            finally:
                with self._enrichment_lock:
                    self._enrichment_inflight.discard(key)
                    self._enrichment_attempted.add(key)

        threading.Thread(target=worker, daemon=True, name="groovia-lyrics-enrichment").start()
        return True

    def enrich_tracks_async(self, tracks, callback=None) -> int:
        """Start enrichment for each final imported track and return starts."""
        started = 0
        for track in tracks or ():
            started += int(self.enrich_track_async(track, callback=callback))
        return started

    def ingest_download(
        self, track, lyrics_path: str | Path | None, *, provider="external", replace=False
    ):
        """Ingest a sidecar supplied by the user/library import, never spotDL lyrics."""
        if track.id is None or not lyrics_path:
            return None
        path = Path(lyrics_path)
        timeline = self._read_file(path, provider)
        if timeline is None:
            return None
        self._save_mapping(track, path, timeline, provider=provider, replace=replace)
        return timeline

    def import_file(self, track, source: str | Path, *, user_edited=True):
        if track.id is None:
            return None
        source = Path(source)
        timeline = self._read_file(source, "manual")
        if timeline is None:
            return None
        suffix = (
            ".ttml"
            if timeline.source_format == "ttml"
            else ".lrc"
            if timeline.synchronized
            else ".txt"
        )
        destination = self.root / f"track-{track.id}{suffix}"
        try:
            shutil.copyfile(source, destination)
            self._save_mapping(
                track,
                destination,
                timeline,
                provider="manual",
                user_edited=user_edited,
                replace=True,
            )
        except OSError:
            return None
        return self._read_file(destination, "manual")

    def save_text(self, track, content: str, *, synchronized: bool = False, user_edited=True):
        if track.id is None:
            return None
        suffix = ".lrc" if synchronized else ".txt"
        destination = self.root / f"track-{track.id}{suffix}"
        try:
            destination.write_text(content, encoding="utf-8")
        except OSError:
            return None
        timeline = parse_lyrics(content, provider="manual", file_path=str(destination))
        self._save_mapping(
            track,
            destination,
            timeline,
            provider="manual",
            user_edited=user_edited,
            replace=True,
        )
        return timeline

    @staticmethod
    def _mode(timeline: LyricsTimeline) -> str:
        if timeline.word_synchronized:
            return "word"
        if timeline.synchronized:
            return "line"
        return "plain"

    @staticmethod
    def _bundle_quality(bundle: LyricsBundle | None) -> int:
        """Return the best synchronization quality present in a bundle.

        ``2`` is word/syllable synchronization, ``1`` is line
        synchronization, and ``0`` is plain text.  A missing bundle is ``-1``.
        Keeping this separate from provider priority lets Better Lyrics remain
        primary while still allowing a synchronized LRCLIB result to beat a
        plain Better Lyrics response.
        """
        if not bundle:
            return -1
        timelines = (bundle.word, bundle.line, bundle.plain)
        if any(timeline and timeline.word_synchronized for timeline in timelines):
            return 2
        if any(timeline and timeline.synchronized for timeline in timelines):
            return 1
        return 0

    @staticmethod
    def _candidate_priority(item) -> tuple[int, int]:
        timeline, row = item
        quality = {"word": 0, "line": 1, "plain": 2}[LyricsService._mode(timeline)]
        return quality, 0 if row.get("user_edited") else 1

    def _load_candidates(self, track):
        if track.id is None:
            return []
        rows = self.database.lyrics_for_track(track.id)
        candidates = []
        for row in rows:
            path = Path(row["file_path"]) if row.get("file_path") else None
            timeline = (
                self._read_file(path, row.get("provider")) if path and path.is_file() else None
            )
            if timeline is None and row.get("content"):
                try:
                    timeline = parse_lyrics(row["content"], provider=row.get("provider"))
                except LyricsParseError:
                    timeline = None
            if timeline:
                timeline.apply_offset(timeline.offset_ms + int(row.get("timing_offset_ms") or 0))
                timeline.user_edited = bool(row.get("user_edited"))
                candidates.append((timeline, row))
        if candidates:
            return candidates

        if not track.path.startswith(("http://", "https://")):
            audio = Path(track.path)
            for candidate in (
                audio.with_suffix(".ttml"),
                audio.with_suffix(".xml"),
                audio.with_suffix(".lrc"),
                audio.with_suffix(".txt"),
            ):
                if candidate.is_file():
                    timeline = self._read_file(candidate)
                    if timeline:
                        self._save_mapping(track, candidate, timeline, provider="external")
                        return self._load_candidates(track)
            embedded = self.scanner.read_embedded_lyrics(track.path)
            if embedded:
                try:
                    return [
                        (
                            parse_lyrics(embedded, provider="embedded"),
                            {
                                "kind": "plain",
                                "provider": "embedded",
                                "user_edited": False,
                            },
                        )
                    ]
                except LyricsParseError:
                    pass
        return []

    def find_variants(self, track) -> list[tuple[LyricsTimeline, dict]]:
        candidates = self._load_candidates(track)
        best: dict[str, tuple[LyricsTimeline, dict]] = {}
        for candidate in candidates:
            timeline, row = candidate
            mode = self._mode(timeline)
            if mode not in best or self._candidate_priority(candidate) < self._candidate_priority(
                best[mode]
            ):
                best[mode] = candidate
            if mode == "word":
                line = timeline.line_view()
                line.user_edited = timeline.user_edited
                line_candidate = (line, row)
                if "line" not in best or self._candidate_priority(
                    line_candidate
                ) < self._candidate_priority(best["line"]):
                    best["line"] = line_candidate
        if "line" in best or "word" in best:
            return [best[mode] for mode in ("line", "word") if mode in best]
        return [best["plain"]] if "plain" in best else []

    def find(self, track) -> tuple[LyricsTimeline | None, dict | None]:
        variants = self.find_variants(track)
        if not variants:
            return None, None
        return min(variants, key=self._candidate_priority)

    def remove(self, track, *, include_user_edited=False):
        rows = self.database.lyrics_for_track(track.id)
        for row in rows:
            if row["user_edited"] and not include_user_edited:
                continue
            path = Path(row["file_path"]) if row.get("file_path") else None
            if path and (
                self._within(path, self.root)
                or path.suffix.lower() in {".lrc", ".txt", ".ttml", ".xml"}
            ):
                try:
                    if self.database.lyrics_path_references(str(path)) <= 1:
                        path.unlink()
                except OSError:
                    pass
            self.database.delete_lyrics(row["id"])

    def cleanup_missing_managed(self, root: str | Path):
        managed_root = Path(root)
        for track in self.database.all_tracks():
            for row in self.database.lyrics_for_track(track.id):
                path = Path(row["file_path"]) if row.get("file_path") else None
                if (
                    path
                    and self._within(path, managed_root)
                    and not path.exists()
                    and not row["user_edited"]
                ):
                    self.database.delete_lyrics(row["id"])

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False
