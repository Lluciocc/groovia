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

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from .parser import LyricsTimeline, parse_lyrics
from ..platform_compat import get_data_dir


@dataclass(slots=True)
class LyricsBundle:
    """The available synced forms for one track."""

    word: LyricsTimeline | None = None
    line: LyricsTimeline | None = None

    @property
    def preferred(self) -> LyricsTimeline | None:
        return self.line or self.word

    def __bool__(self):
        return bool(self.word or self.line)


class LyricsService:
    def __init__(self, database, scanner, data_dir: str | Path | None = None):
        self.database = database
        self.scanner = scanner
        self.root = (
            Path(data_dir) / "groovia" / "lyrics"
            if data_dir
            else get_data_dir() / "lyrics"
        )
        self.root.mkdir(parents=True, exist_ok=True)
        # Keep the custom Musixmatch workaround as the single Musixmatch
        # implementation.  Import lazily so the lyrics package remains usable
        # by the download package without creating an import cycle.
        try:
            from ..downloads.musicmatch import MusixmatchRichsync

            self.musixmatch = MusixmatchRichsync(
                token_cache=self.root / "musixmatch-token.json"
            )
        except (ImportError, OSError, TypeError):
            self.musixmatch = None

    @staticmethod
    def _checksum(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _kind(path: Path, timeline: LyricsTimeline) -> str:
        return (
            "synced"
            if timeline.synchronized and path.suffix.lower() == ".lrc"
            else "plain"
        )

    def _read_file(
        self, path: Path, provider: str | None = None
    ) -> LyricsTimeline | None:
        try:
            content = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            return None
        if not content.strip():
            return None
        return parse_lyrics(content, file_path=str(path), provider=provider)

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
        content = path.read_text(encoding="utf-8-sig")
        kind = self._kind(path, timeline)
        existing = self.database.lyrics_for_track(track.id)
        if not replace and any(
            item["user_edited"] and item.get("file_path") == str(path)
            for item in existing
        ):
            return
        self.database.save_lyrics(
            track.id,
            kind,
            str(path),
            provider or timeline.provider,
            timeline.language,
            content,
            user_edited=user_edited,
            # The LRC metadata offset is already part of the parsed timeline.
            # Store only the user-adjustable offset here so it is not applied
            # a second time when the document is loaded again.
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
    ):
        """Persist provider output through the same repository as LRC files."""
        if track.id is None or not content or not content.strip():
            return None
        timeline = parse_lyrics(content, provider=provider)
        if not timeline.lines:
            return None
        kind = "synced" if timeline.synchronized else "plain"
        suffix = ".lrc" if timeline.synchronized else ".txt"
        safe_provider = (
            "".join(char for char in provider.lower() if char.isalnum() or char in "-_")
            or "lyrics"
        )
        safe_variant = "".join(
            char for char in (variant or "").lower() if char.isalnum() or char in "-_"
        )
        variant_suffix = f"-{safe_variant}" if safe_variant else ""
        destination = (
            self.root / f"track-{track.id}-{safe_provider}{variant_suffix}{suffix}"
        )
        try:
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
        return self._read_file(destination, provider)

    def fetch_musixmatch(self, track, *, replace: bool = False) -> LyricsBundle | None:
        """Fetch and persist only Musixmatch richsync lyrics."""
        if not self.musixmatch or track.id is None:
            return None
        try:
            result = self.musixmatch.get_lyrics(
                track.title,
                track.artist,
                getattr(track, "album", "") or "",
                getattr(track, "duration", 0) or 0,
                words_only=True,
            )
        except Exception:
            return None
        if not result or not result.is_word_synced:
            return None
        bundle = LyricsBundle()
        bundle.word = self.ingest_content(
            track,
            result.lrc,
            provider="musixmatch",
            source_id=f"{result.commontrack_id}:word",
            replace=replace,
            variant="word",
        )
        return bundle if bundle else None

    def ingest_download(
        self, track, lrc_path: str | Path | None, *, provider="spotdl", replace=False
    ):
        if track.id is None or not lrc_path:
            return None
        path = Path(lrc_path)
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
        suffix = ".lrc" if timeline.synchronized else ".txt"
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
        return timeline

    def save_text(
        self, track, content: str, *, synchronized: bool = False, user_edited=True
    ):
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
    def _displayable(timeline: LyricsTimeline, row: dict) -> bool:
        # Older builds stored Musixmatch's subtitle response as a line lyric.
        # Line mode now belongs exclusively to the fallback provider, so do
        # not resurrect those stale entries after upgrading.
        return not (
            LyricsService._mode(timeline) == "line"
            and str(row.get("provider") or "").lower() == "musixmatch"
        )

    @staticmethod
    def _candidate_priority(item) -> tuple[int, int]:
        timeline, row = item
        return (
            (
                0
                if LyricsService._mode(timeline) == "line"
                else (1 if LyricsService._mode(timeline) == "word" else 2)
            ),
            0 if row.get("user_edited") else 1,
        )

    def _load_candidates(self, track):
        if track.id is None:
            return []
        rows = self.database.lyrics_for_track(track.id)
        candidates = []
        for row in rows:
            path = Path(row["file_path"]) if row.get("file_path") else None
            if path and path.is_file():
                timeline = self._read_file(path, row.get("provider"))
                if timeline:
                    timeline.apply_offset(
                        timeline.offset_ms + int(row.get("timing_offset_ms") or 0)
                    )
                    timeline.user_edited = bool(row.get("user_edited"))
                    if self._displayable(timeline, row):
                        candidates.append((timeline, row))
                    continue
            if row.get("content"):
                timeline = parse_lyrics(row["content"], provider=row.get("provider"))
                timeline.apply_offset(
                    timeline.offset_ms + int(row.get("timing_offset_ms") or 0)
                )
                timeline.user_edited = bool(row.get("user_edited"))
                if self._displayable(timeline, row):
                    candidates.append((timeline, row))

        if candidates:
            return candidates

        if not track.path.startswith(("http://", "https://")):
            audio = Path(track.path)
            for candidate in (audio.with_suffix(".lrc"), audio.with_suffix(".txt")):
                if candidate.is_file():
                    timeline = self._read_file(candidate)
                    if timeline:
                        self._save_mapping(
                            track, candidate, timeline, provider="external"
                        )
                        return self._load_candidates(track)
            embedded = self.scanner.read_embedded_lyrics(track.path)
            if embedded:
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
        return []

    def find_variants(self, track) -> list[tuple[LyricsTimeline, dict]]:
        """Return one best candidate per display mode.

        When synchronized lyrics exist, plain lyrics are deliberately omitted;
        the view then exposes line and word modes only.
        """
        candidates = self._load_candidates(track)
        best: dict[str, tuple[LyricsTimeline, dict]] = {}
        for candidate in candidates:
            timeline, _row = candidate
            mode = self._mode(timeline)
            if mode not in best or self._candidate_priority(
                candidate
            ) < self._candidate_priority(best[mode]):
                best[mode] = candidate
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
                self._within(path, self.root) or path.suffix.lower() in {".lrc", ".txt"}
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

    def _within(self, path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False
