"""Lyrics file discovery, persistence and spotDL output ingestion."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from .parser import LyricsTimeline, parse_lyrics


class LyricsService:
    def __init__(self, database, scanner, data_dir: str | Path | None = None):
        self.database = database
        self.scanner = scanner
        base = Path(data_dir or os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        self.root = base / "groovia" / "lyrics"
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _checksum(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _kind(path: Path, timeline: LyricsTimeline) -> str:
        return "synced" if timeline.synchronized and path.suffix.lower() == ".lrc" else "plain"

    def _read_file(self, path: Path, provider: str | None = None) -> LyricsTimeline | None:
        try:
            content = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            return None
        if not content.strip():
            return None
        return parse_lyrics(content, file_path=str(path), provider=provider)

    def _save_mapping(self, track, path: Path, timeline: LyricsTimeline, *, provider=None,
                      user_edited=False, replace=False):
        content = path.read_text(encoding="utf-8-sig")
        kind = self._kind(path, timeline)
        existing = self.database.lyrics_for_track(track.id)
        if not replace and any(item["user_edited"] and item["kind"] == kind for item in existing):
            return
        self.database.save_lyrics(
            track.id, kind, str(path), provider or timeline.provider,
            timeline.language, content, user_edited=user_edited,
            timing_offset_ms=timeline.offset_ms, checksum=self._checksum(content),
        )

    def ingest_download(self, track, lrc_path: str | Path | None, *, provider="spotdl", replace=False):
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
            self._save_mapping(track, destination, timeline, provider="manual", user_edited=user_edited, replace=True)
        except OSError:
            return None
        return timeline

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
        self._save_mapping(track, destination, timeline, provider="manual", user_edited=user_edited, replace=True)
        return timeline

    def find(self, track) -> tuple[LyricsTimeline | None, dict | None]:
        if track.id is None:
            return None, None
        rows = self.database.lyrics_for_track(track.id)
        for row in sorted(rows, key=lambda item: (not item["user_edited"], item["kind"] != "synced")):
            path = Path(row["file_path"]) if row.get("file_path") else None
            if path and path.is_file():
                timeline = self._read_file(path, row.get("provider"))
                if timeline:
                    timeline.offset_ms += int(row.get("timing_offset_ms") or 0)
                    timeline.user_edited = bool(row.get("user_edited"))
                    return timeline, row
            if row.get("content"):
                timeline = parse_lyrics(row["content"], provider=row.get("provider"))
                timeline.offset_ms += int(row.get("timing_offset_ms") or 0)
                return timeline, row

        if not track.path.startswith(("http://", "https://")):
            audio = Path(track.path)
            for candidate in (audio.with_suffix(".lrc"), audio.with_suffix(".txt")):
                if candidate.is_file():
                    timeline = self._read_file(candidate)
                    if timeline:
                        self._save_mapping(track, candidate, timeline, provider="external")
                        return timeline, self.database.lyrics_for_track(track.id)[-1]
            embedded = self.scanner.read_embedded_lyrics(track.path)
            if embedded:
                return parse_lyrics(embedded, provider="embedded"), {"kind": "plain", "provider": "embedded", "user_edited": False}
        return None, None

    def remove(self, track, *, include_user_edited=False):
        rows = self.database.lyrics_for_track(track.id)
        for row in rows:
            if row["user_edited"] and not include_user_edited:
                continue
            path = Path(row["file_path"]) if row.get("file_path") else None
            if path and (self._within(path, self.root) or path.suffix.lower() in {".lrc", ".txt"}):
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
                if path and self._within(path, managed_root) and not path.exists() and not row["user_edited"]:
                    self.database.delete_lyrics(row["id"])

    def _within(self, path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False
