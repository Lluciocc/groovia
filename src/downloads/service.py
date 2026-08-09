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

import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from gi.repository import GLib

from ..lyrics import LyricsService
from ..platform_compat import get_data_dir, get_music_dir
from .importer import SpotDLImportService
from .manager import DownloadManager
from .spotdl import SourceInfo, classify_input, read_sync_metadata, read_sync_source


class SpotDLService:
    def __init__(self, database, scanner, data_dir: str | Path | None = None, callback=None):
        self.database = database
        self.scanner = scanner
        self.callback = callback
        self.manager = DownloadManager(data_dir, self._manager_event, database)
        self.lyrics = LyricsService(database, scanner, data_dir)
        self.importer = SpotDLImportService(database, scanner, self._import_event, self.lyrics)
        self._contexts: dict[str, dict] = {}

    @property
    def data_root(self) -> Path:
        return get_data_dir()

    @property
    def music_dir(self) -> Path:
        configured = os.environ.get("GROOVIA_MUSIC_DIR")
        return (
            Path(configured).expanduser().resolve() if configured else get_music_dir() / "Groovia"
        )

    @property
    def sync_root(self) -> Path:
        return self.music_dir / "Synced Playlists"

    def classify(self, value: str) -> SourceInfo:
        return classify_input(value)

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def submit(
        self,
        value: str,
        sync_enabled: bool = True,
        sync_mode: str = "safe",
        existing_action: str | None = None,
        playlist_id: int | None = None,
        output_format: str = "mp3",
        bitrate: str = "auto",
        cover_policy: str = "follow",
        order_policy: str = "spotify",
        lyrics_mode: str = "none",
        lyrics_fallback: bool = True,
        generate_lrc: bool = False,
        lyrics_providers: tuple[str, ...] = (),
        sync_remove_lrc: bool = False,
    ):
        info = self.classify(value)
        if info.kind == "invalid":
            self._emit(
                "input-error",
                None,
                {"message": "Enter a Spotify track, Spotify playlist or .spotdl file."},
            )
            return None
        if (
            info.kind == "sync"
            and not read_sync_metadata(info.value)
            and not read_sync_source(info.value)[0]
        ):
            self._emit(
                "input-error",
                None,
                {"message": "This .spotdl file is invalid or cannot be read."},
            )
            return None
        playlist = None
        if info.kind in {"playlist", "album"}:
            playlist = self.database.playlist_by_source(info.spotify_id, info.value)
        elif info.kind == "sync":
            sync_url = None
            sync_url, sync_id = read_sync_source(info.value)
            playlist = self.database.playlist_by_source(sync_id, sync_url)
            for candidate in self.database.playlists():
                if (
                    playlist is None
                    and candidate.sync_file
                    and Path(candidate.sync_file).resolve() == Path(info.value).resolve()
                ):
                    playlist = candidate
                    break
            if playlist is None:
                info = SourceInfo("sync", info.value, sync_id)
        if playlist and existing_action is None:
            self._emit("conflict", None, {"playlist": playlist, "source": info, "value": value})
            return None
        context = {
            "info": info,
            "replace": existing_action == "replace",
            "playlist": playlist,
        }
        if info.kind == "track":
            destination = self.music_dir
            job_type = "track"
            source = info.value
            sync_file = None
        else:
            if playlist is None:
                if existing_action == "duplicate":
                    name = self._unique_name(
                        f"Spotify {info.kind.title()} {info.spotify_id or 'Import'}"
                    )
                else:
                    name = f"Spotify {info.kind.title()} {info.spotify_id or 'Import'}"
                destination = self.sync_root / (info.spotify_id or "imported")
                sync_file = self.data_root / "sync" / f"{info.spotify_id or 'imported'}.spotdl"
                destination.mkdir(parents=True, exist_ok=True)
                sync_file.parent.mkdir(parents=True, exist_ok=True)
                playlist = self.database.create_playlist(
                    name,
                    source_url=(info.value if info.kind in {"playlist", "album"} else sync_url),
                    source_id=info.spotify_id,
                    sync_file=str(sync_file),
                    managed_dir=str(destination),
                    sync_mode=sync_mode,
                    cover_policy=cover_policy,
                    order_policy=order_policy,
                    sync_status="synchronizing",
                )
            else:
                destination = Path(playlist.managed_dir or self.sync_root / str(playlist.id))
                sync_file = (
                    Path(playlist.sync_file)
                    if playlist.sync_file
                    else self.data_root / "sync" / f"{playlist.id}.spotdl"
                )
                if existing_action == "replace":
                    self.database.clear_playlist(playlist.id)
                self.database.update_playlist_source(
                    playlist.id,
                    sync_mode=sync_mode,
                    sync_status="synchronizing",
                    sync_file=str(sync_file),
                    managed_dir=str(destination),
                    cover_policy=cover_policy,
                    order_policy=order_policy,
                )
            destination.mkdir(parents=True, exist_ok=True)
            sync_file.parent.mkdir(parents=True, exist_ok=True)
            if sync_mode == "mirror" and not self._within(destination, self.sync_root):
                self._emit(
                    "sync-error",
                    None,
                    {
                        "message": "Mirror synchronization is restricted to Groovia-managed directories."
                    },
                )
                return None
            context["playlist"] = playlist
            context["sync_enabled"] = sync_enabled
            job_type = "sync" if sync_enabled or info.kind == "sync" else "playlist"
            source = info.value
            if info.kind == "sync":
                job_type = "sync"
        job = self.manager.submit(
            job_type,
            source,
            destination,
            sync_file if job_type == "sync" else None,
            sync_mode=sync_mode,
            output_format=output_format,
            bitrate=bitrate,
            playlist_id=playlist.id if playlist else playlist_id,
            lyrics_mode=lyrics_mode,
            lyrics_fallback=lyrics_fallback,
            generate_lrc=generate_lrc,
            lyrics_providers=lyrics_providers,
            sync_remove_lrc=sync_remove_lrc,
        )
        self._contexts[job.id] = context
        return job

    def synchronize(
        self,
        playlist_id: int,
        sync_mode: str | None = None,
        output_format: str = "mp3",
        bitrate: str = "auto",
        lyrics_mode: str = "none",
        lyrics_fallback: bool = True,
        generate_lrc: bool = False,
        lyrics_providers: tuple[str, ...] = (),
        sync_remove_lrc: bool = False,
    ):
        playlist = self.database.playlist(playlist_id)
        if not playlist or not playlist.sync_file:
            self._emit(
                "sync-error",
                None,
                {
                    "playlist_id": playlist_id,
                    "message": "Synchronization data is missing.",
                },
            )
            return None
        mode = sync_mode or playlist.sync_mode
        destination = Path(playlist.managed_dir or self.sync_root / str(playlist.id))
        if mode == "mirror" and not self._within(destination, self.sync_root):
            self._emit(
                "sync-error",
                None,
                {
                    "playlist_id": playlist_id,
                    "message": "Mirror synchronization is restricted to Groovia-managed directories.",
                },
            )
            return None
        self.database.update_playlist_source(
            playlist_id, sync_status="synchronizing", sync_mode=mode
        )
        job = self.manager.submit(
            "sync",
            playlist.sync_file,
            destination,
            playlist.sync_file,
            sync_mode=mode,
            output_format=output_format,
            bitrate=bitrate,
            playlist_id=playlist.id,
            lyrics_mode=lyrics_mode,
            lyrics_fallback=lyrics_fallback,
            generate_lrc=generate_lrc,
            lyrics_providers=lyrics_providers,
            sync_remove_lrc=sync_remove_lrc,
        )
        self._contexts[job.id] = {
            "playlist": playlist,
            "info": SourceInfo("sync", playlist.sync_file),
            "replace": False,
        }
        return job

    def disconnect(self, playlist_id: int):
        self.database.update_playlist_source(
            playlist_id, sync_status="disconnected", auto_sync="manual"
        )

    def find_lyrics(self, track, *, providers: tuple[str, ...] = (), fallback: bool = True):
        """Find lyrics asynchronously, preferring Groovia's richsync backend."""
        if not track.spotify_id:
            self._emit(
                "lyrics-error",
                None,
                {
                    "track": track,
                    "message": "This track has no Spotify source mapping.",
                },
            )
            return None
        source = f"https://open.spotify.com/track/{track.spotify_id}"
        destination = Path(track.path).parent
        selected = tuple(providers or ("synced", "genius", "musixmatch", "azlyrics"))
        if "musixmatch" in {provider.lower() for provider in selected}:

            def worker():
                bundle = self.lyrics.fetch_musixmatch(track)
                if bundle:
                    GLib.idle_add(
                        self._emit,
                        "lyrics-completed",
                        None,
                        {
                            "track": track,
                            "timeline": bundle.preferred,
                            "bundle": bundle,
                        },
                    )
                    if fallback:
                        self._submit_lyrics_fallback(track, source, destination, selected)
                    return
                if fallback:
                    self._submit_lyrics_fallback(track, source, destination, selected)
                else:
                    GLib.idle_add(
                        self._emit,
                        "lyrics-failed",
                        None,
                        {"track": track, "error": "Musixmatch returned no lyrics."},
                    )

            threading.Thread(target=worker, daemon=True, name="groovia-musixmatch-lyrics").start()
            # Keep the public contract truthy so batch callers can report that
            # a search was queued even though no spotDL job exists yet.
            return True

        job = self.manager.submit(
            "lyrics",
            source,
            destination,
            sync_mode="safe",
            output_format="mp3",
            bitrate="auto",
            lyrics_mode="synced",
            lyrics_fallback=fallback,
            generate_lrc=True,
            lyrics_providers=selected,
        )
        self._contexts[job.id] = {"track": track}
        return job

    def _submit_lyrics_fallback(self, track, source, destination, selected):
        providers = tuple(
            provider for provider in selected if provider.lower() != "musixmatch"
        ) or ("synced", "genius", "azlyrics")
        job = self.manager.submit(
            "lyrics",
            source,
            destination,
            sync_mode="safe",
            output_format="mp3",
            bitrate="auto",
            lyrics_mode="synced",
            lyrics_fallback=True,
            generate_lrc=True,
            lyrics_providers=providers,
        )
        self._contexts[job.id] = {"track": track}

    def _unique_name(self, base: str) -> str:
        names = {item.name for item in self.database.playlists()}
        name = base
        index = 2
        while name in names:
            name = f"{base} {index}"
            index += 1
        return name

    def _manager_event(self, event, job, payload):
        if job and job.job_type == "lyrics" and event == "finished":
            context = self._contexts.get(job.id, {})
            track = context.get("track")
            lrc_path = Path(track.path).with_suffix(".lrc") if track else None
            timeline = (
                self.lyrics.ingest_download(track, lrc_path)
                if track and lrc_path and lrc_path.exists()
                else None
            )
            self._emit("lyrics-completed", job, {"track": track, "timeline": timeline})
            return
        if job and job.job_type == "lyrics" and event in {"failed", "cancelled"}:
            self._emit(
                "lyrics-failed",
                job,
                {
                    "track": self._contexts.get(job.id, {}).get("track"),
                    "error": job.error or event,
                },
            )
            return
        if event == "finished":
            self.importer.import_async(job, payload)
        elif event in {"failed", "cancelled"}:
            if payload.get("files"):
                self.importer.import_async(job, payload)
            else:
                context = self._contexts.get(job.id, {})
                playlist = context.get("playlist")
                if playlist:
                    self.database.update_playlist_source(
                        playlist.id,
                        sync_status="cancelled" if event == "cancelled" else "failed",
                        last_sync_result=job.error or event,
                    )
                self._emit(event, job, payload)
        else:
            self._emit(event, job, payload)

    def _import_event(self, event, job, payload):
        if event != "import-finished":
            self._emit(event, job, payload)
            return
        context = self._contexts.get(job.id, {})
        playlist = context.get("playlist")
        tracks = payload.get("tracks", [])
        if playlist:
            metadata = payload.get("metadata", [])
            cover_path = payload.get("cover_path")
            if cover_path and playlist.cover_policy == "follow":
                self.database.update_playlist_cover(playlist.id, cover_path)
                playlist = self.database.playlist(playlist.id)
            playlist_name = payload.get("playlist_name") or next(
                (item.get("list_name") for item in metadata if item.get("list_name")),
                None,
            )
            if (
                playlist_name
                and playlist.name.startswith("Spotify Playlist")
                and not playlist.is_favorites
            ):
                try:
                    self.database.rename_playlist(playlist.id, playlist_name)
                    playlist = self.database.playlist(playlist.id)
                except Exception:
                    pass
            by_source = {track.spotify_id: track for track in tracks if track.spotify_id}
            ordered = [
                by_source[item["spotify_id"]]
                for item in metadata
                if item["spotify_id"] in by_source
            ]
            ordered.extend(track for track in tracks if track not in ordered)
            if ordered:
                self.database.add_tracks_to_playlist(playlist.id, ordered)
                if playlist.order_policy == "spotify":
                    self.database.reorder_playlist(
                        playlist.id,
                        [track.id for track in ordered if track.id is not None],
                    )
                if job.state == "finished" and job.job_type == "sync" and metadata:
                    wanted = {track.id for track in ordered if track.id is not None}
                    for track_id in self.database.playlist_track_ids(playlist.id):
                        if track_id not in wanted:
                            self.database.remove_track_from_playlist(playlist.id, track_id)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.database.update_playlist_source(
                playlist.id,
                sync_status="synchronized" if job.state == "finished" else "failed",
                last_sync_at=now if job.state == "finished" else playlist.last_sync_at,
                last_sync_result=(
                    f"{len(ordered)} tracks imported"
                    if job.state == "finished"
                    else (job.error or "partial failure")
                ),
            )
            if (
                job.state == "finished"
                and job.job_type == "sync"
                and job.sync_mode == "mirror"
                and job.sync_remove_lrc
            ):
                self.lyrics.cleanup_missing_managed(job.destination)
        terminal_event = (
            "completed"
            if job.state == "finished"
            else ("cancelled" if job.state == "cancelled" else "failed")
        )
        self._emit(
            terminal_event,
            job,
            {
                "tracks": tracks,
                "playlist": playlist,
                "metadata": payload.get("metadata", []),
                "count": len(tracks),
                "lyrics_counts": payload.get("lyrics_counts", {}),
            },
        )

    def _emit(self, event, job, payload):
        if self.callback:
            self.callback(event, job, payload)
