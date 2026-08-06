"""Import spotDL output into Groovia's existing library and playlist tables."""

from __future__ import annotations

import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import json
from pathlib import Path
from typing import Callable

from ..models import Track
from .spotdl import AUDIO_SUFFIXES, read_sync_metadata
from ..lyrics import LyricsService


def _key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


class DownloadedTrackImporter:
    def __init__(self, database, scanner, lyrics_service=None):
        self.database = database
        self.scanner = scanner
        self.lyrics_service = lyrics_service
        self.lyrics_counts = {"synced": 0, "plain": 0, "failed": 0}

    def import_files(self, files: set[str], metadata: list[dict] | None = None,
                     job=None, progress_callback=None,
                     existing_files: set[str] | None = None) -> list[Track]:
        metadata = metadata or []
        self.lyrics_counts = {"synced": 0, "plain": 0, "failed": 0}
        unused = list(metadata)
        imported: list[Track] = []
        existing_files = {
            str(Path(path).resolve()) for path in (existing_files or ())
        }
        audio_paths = [
            Path(raw_path) for raw_path in sorted(files)
            if Path(raw_path).is_file() and Path(raw_path).suffix.lower() in AUDIO_SUFFIXES
        ]
        total = max(len(audio_paths), len(metadata))
        processed = 0
        if progress_callback:
            progress_callback(0, total, "Preparing library import", "Preparing")
        for path in audio_paths:
            # A safe playlist sync may report no new files because spotDL
            # skipped every download as a duplicate.  Those existing files
            # are useful only when they match an entry from the sync manifest;
            # do not accidentally add unrelated audio from the same folder.
            is_reused_file = str(path.resolve()) in existing_files
            try:
                scanned = self.scanner.read_track(str(path))
            except Exception:
                processed += 1
                if progress_callback:
                    progress_callback(processed, total, path.name, "Skipped")
                continue
            match = self._match_metadata(scanned, unused)
            if is_reused_file and not match:
                processed += 1
                if progress_callback:
                    progress_callback(processed, total, path.name, "Skipped")
                continue
            if match:
                scanned.spotify_id = match.get("spotify_id")
                scanned.isrc = match.get("isrc")
                unused.remove(match)
            existing = None
            if scanned.spotify_id:
                existing = self.database.track_by_spotify_id(scanned.spotify_id)
            if existing is None and scanned.isrc:
                existing = self.database.track_by_isrc(scanned.isrc)
            if existing is None:
                existing = self.database.track_by_metadata(scanned)
            if existing and Path(existing.path).exists():
                imported.append(existing)
                if scanned.spotify_id:
                    self.database.save_track_source(scanned.spotify_id, existing, scanned.isrc)
                self._ingest_lyrics(existing, path, job=job)
                if Path(scanned.path).resolve() != Path(existing.path).resolve():
                    try:
                        Path(scanned.path).unlink()
                    except OSError:
                        pass
                processed += 1
                if progress_callback:
                    progress_callback(processed, total, existing.title, "Imported")
                continue
            self.database.upsert_tracks([scanned])
            stored = self.database.track_by_path(scanned.path) or scanned
            imported.append(stored)
            self._ingest_lyrics(stored, path, job=job)
            if scanned.spotify_id:
                self.database.save_track_source(scanned.spotify_id, stored, scanned.isrc)
            processed += 1
            if progress_callback:
                progress_callback(processed, total, stored.title, "Imported")
        # A safe sync often has no new files at all. Reuse the existing library
        # entries described by the refreshed .spotdl file so playlist removals
        # and order changes can still be applied without reimporting audio.
        known_ids = {track.spotify_id for track in imported if track.spotify_id}
        for item in metadata:
            spotify_id = item.get("spotify_id")
            if spotify_id and spotify_id not in known_ids:
                existing = self.database.track_by_spotify_id(spotify_id)
                if existing:
                    imported.append(existing)
                    known_ids.add(spotify_id)
                    processed += 1
                    if progress_callback:
                        progress_callback(min(processed, total), total, existing.title, "Reused from library")
        # A sync can reuse an existing library track without producing a new
        # audio file. Fetch the selected custom provider for those entries too.
        if job and self.lyrics_service and self._uses_musixmatch(job):
            for track in imported:
                if not track.id:
                    continue
                variants = self.lyrics_service.find_variants(track)
                modes = {self.lyrics_service._mode(timeline) for timeline, _row in variants}
                if "word" not in modes:
                    bundle = self.lyrics_service.fetch_musixmatch(track)
                    if bundle:
                        self.lyrics_counts["synced"] += 1
                    else:
                        self.lyrics_counts["failed"] += 1
        return imported

    @staticmethod
    def _uses_musixmatch(job) -> bool:
        selected = job.lyrics_providers or ("synced", "genius", "musixmatch", "azlyrics")
        return (
            job.lyrics_mode != "none"
            and "musixmatch" in {str(provider).lower() for provider in selected}
        )

    def _ingest_lyrics(self, track, audio_path: Path, *, job=None):
        if not self.lyrics_service:
            return
        if job and self._uses_musixmatch(job):
            variants = self.lyrics_service.find_variants(track)
            modes = {self.lyrics_service._mode(timeline) for timeline, _row in variants}
            providers = {row.get("provider") for _timeline, row in variants}
            if {"line", "word"}.issubset(modes) and "musixmatch" in providers:
                return
            bundle = self.lyrics_service.fetch_musixmatch(track)
            if bundle:
                self.lyrics_counts["synced"] += 1
        for candidate in (audio_path.with_suffix(".lrc"), audio_path.with_suffix(".txt")):
            if not candidate.is_file():
                continue
            timeline = self.lyrics_service.ingest_download(track, candidate)
            if timeline:
                self.lyrics_counts["synced" if timeline.synchronized else "plain"] += 1
            else:
                self.lyrics_counts["failed"] += 1
            return

    @staticmethod
    def _match_metadata(track: Track, metadata: list[dict]) -> dict | None:
        if not metadata:
            return None
        target = (_key(track.title), _key(track.artist), _key(track.album))
        for item in metadata:
            title = _key(item.get("title"))
            artist = _key(item.get("artist"))
            album = _key(item.get("album"))
            if title and title == target[0] and (not artist or artist in target[1] or target[1] in artist):
                return item
            if title and title == target[0] and album and album == target[2]:
                return item
        return metadata[0] if len(metadata) == 1 else None


class SpotDLImportService:
    """Connect subprocess completion to the database without blocking GTK."""

    def __init__(self, database, scanner, callback: Callable | None = None,
                 lyrics_service: LyricsService | None = None):
        self.database = database
        self.scanner = scanner
        self.callback = callback
        self.importer = DownloadedTrackImporter(database, scanner, lyrics_service)
        self.lyrics_service = lyrics_service

    def import_async(self, job, payload: dict):
        thread = threading.Thread(
            target=self._worker, args=(job, payload), daemon=True,
            name=f"groovia-import-{job.id[:8]}",
        )
        thread.start()

    def _worker(self, job, payload):
        self._emit("import-started", job, {})
        sync_file = job.sync_file if job.sync_file and job.sync_file.exists() else None
        metadata = read_sync_metadata(sync_file) if sync_file else []
        new_files = {
            str(Path(path).resolve()) for path in payload.get("files", set())
        }
        existing_files = set()
        if job.job_type in {"sync", "playlist"} and metadata:
            # DownloadManager intentionally reports only files created during
            # this run.  Include the already-present audio files for playlist
            # imports so an all-duplicates run can rebuild the DB/playlist
            # association from the authoritative .spotdl manifest.
            existing_files = {
                str(path.resolve()) for path in job.destination.rglob("*")
                if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
            } - new_files
        files = new_files | existing_files
        tracks = self.importer.import_files(
            files, metadata, job=job,
            existing_files=existing_files,
            progress_callback=lambda current, total, title, phase: self._emit(
                "import-progress", job,
                {"current": current, "total": total, "title": title, "phase": phase},
            ),
        )
        playlist_name, cover_url = self._playlist_oembed(job.source) if job.job_type in {"sync", "playlist"} else (None, None)
        cover_path = self._download_cover(job, metadata, cover_url)
        self._emit("import-finished", job, {
            "tracks": tracks, "metadata": metadata, "sync_file": str(sync_file) if sync_file else None,
            "cover_path": cover_path, "playlist_name": playlist_name,
            "lyrics_counts": self.importer.lyrics_counts,
        })

    @staticmethod
    def _playlist_oembed(source):
        if not source.startswith("http") or "open.spotify.com/playlist/" not in source:
            return None, None
        endpoint = "https://open.spotify.com/oembed?url=" + urllib.parse.quote(source, safe="")
        try:
            request = urllib.request.Request(endpoint, headers={"User-Agent": "Groovia/0.1"})
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read(512 * 1024).decode("utf-8"))
            return payload.get("title"), payload.get("thumbnail_url")
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
            return None, None

    @staticmethod
    def _download_cover(job, metadata, playlist_cover_url=None):
        cover_url = playlist_cover_url or next((item.get("cover_url") for item in metadata if item.get("cover_url")), None)
        if not cover_url or not job.playlist_id:
            return None
        destination = job.destination.parent / f".groovia-playlist-cover-{job.playlist_id}.jpg"
        try:
            request = urllib.request.Request(cover_url, headers={"User-Agent": "Groovia/0.1"})
            with urllib.request.urlopen(request, timeout=20) as response:
                data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                return None
            destination.write_bytes(data)
            return str(destination)
        except (OSError, ValueError, urllib.error.URLError):
            return None

    def _emit(self, event, job, payload):
        if self.callback:
            from gi.repository import GLib
            GLib.idle_add(self.callback, event, job, payload)
