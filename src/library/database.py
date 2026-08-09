# database.py
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

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from ..models import Playlist, Track
from ..platform_compat import get_data_dir


class LibraryDatabase:
    """Small SQLite store for the local library and playback history."""

    def __init__(self, data_dir: str | None = None):
        self.path = (
            Path(data_dir) / "groovia" / "library.db"
            if data_dir
            else get_data_dir() / "library.db"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS tracks (
              id INTEGER PRIMARY KEY, title TEXT NOT NULL, artist TEXT NOT NULL,
              album TEXT NOT NULL, album_artist TEXT NOT NULL, year TEXT NOT NULL,
              genre TEXT NOT NULL, track_number INTEGER NOT NULL DEFAULT 0,
              disc_number INTEGER NOT NULL DEFAULT 1, duration REAL NOT NULL DEFAULT 0,
              path TEXT NOT NULL UNIQUE, cover_path TEXT, play_count INTEGER NOT NULL DEFAULT 0,
              added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_played TEXT
            );
            CREATE INDEX IF NOT EXISTS tracks_album ON tracks(album, album_artist);
            CREATE TABLE IF NOT EXISTS playlists (
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL UNIQUE,
              cover_path TEXT,
              is_favorites INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              modified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS playlist_tracks (
              playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
              track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
              position INTEGER NOT NULL,
              added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (playlist_id, track_id)
            );
            CREATE INDEX IF NOT EXISTS playlist_tracks_order
              ON playlist_tracks(playlist_id, position);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
        self._migrate_download_schema()
        self.connection.execute(
            "INSERT OR IGNORE INTO playlists(name, is_favorites) VALUES('Favorites', 1)"
        )
        self.connection.commit()

    def _track(self, row: sqlite3.Row) -> Track:
        return Track(**{key: row[key] for key in Track.__dataclass_fields__})

    def _migrate_download_schema(self) -> None:
        """Add download/synchronization data without recreating user data."""
        columns = {
            "tracks": {"spotify_id": "TEXT", "isrc": "TEXT"},
            "playlists": {
                "source_url": "TEXT",
                "source_id": "TEXT",
                "sync_file": "TEXT",
                "managed_dir": "TEXT",
                "sync_mode": "TEXT NOT NULL DEFAULT 'safe'",
                "auto_sync": "TEXT NOT NULL DEFAULT 'manual'",
                "cover_policy": "TEXT NOT NULL DEFAULT 'follow'",
                "order_policy": "TEXT NOT NULL DEFAULT 'spotify'",
                "sync_status": "TEXT NOT NULL DEFAULT 'disconnected'",
                "last_sync_at": "TEXT",
                "last_sync_result": "TEXT",
            },
        }
        for table, wanted in columns.items():
            existing = {
                row[1] for row in self.connection.execute(f"PRAGMA table_info({table})")
            }
            for name, definition in wanted.items():
                if name not in existing:
                    self.connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                    )
        self.connection.executescript("""
            CREATE INDEX IF NOT EXISTS tracks_spotify_id ON tracks(spotify_id);
            CREATE INDEX IF NOT EXISTS playlists_source_id ON playlists(source_id);
            CREATE TABLE IF NOT EXISTS download_jobs (
              id TEXT PRIMARY KEY, job_type TEXT NOT NULL, source TEXT NOT NULL,
              state TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
              destination TEXT, error TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS track_sources (
              spotify_id TEXT PRIMARY KEY, isrc TEXT,
              track_id INTEGER REFERENCES tracks(id) ON DELETE SET NULL,
              local_path TEXT, source_type TEXT NOT NULL DEFAULT 'spotdl',
              downloaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS track_sources_track_id ON track_sources(track_id);
            CREATE TABLE IF NOT EXISTS lyrics (
              id INTEGER PRIMARY KEY,
              track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
              kind TEXT NOT NULL,
              file_path TEXT,
              provider TEXT,
              language TEXT,
              source_id TEXT,
              content TEXT,
              downloaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              modified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              user_edited INTEGER NOT NULL DEFAULT 0,
              timing_offset_ms INTEGER NOT NULL DEFAULT 0,
              checksum TEXT
            );
            CREATE INDEX IF NOT EXISTS lyrics_track ON lyrics(track_id);
            CREATE INDEX IF NOT EXISTS lyrics_kind ON lyrics(track_id, kind);
            """)
        job_columns = {
            "lyrics_mode": "TEXT NOT NULL DEFAULT 'none'",
            "lyrics_fallback": "INTEGER NOT NULL DEFAULT 1",
            "generate_lrc": "INTEGER NOT NULL DEFAULT 0",
            "lyrics_providers": "TEXT",
            "sync_remove_lrc": "INTEGER NOT NULL DEFAULT 0",
        }
        existing_jobs = {
            row[1]
            for row in self.connection.execute("PRAGMA table_info(download_jobs)")
        }
        for name, definition in job_columns.items():
            if name not in existing_jobs:
                self.connection.execute(
                    f"ALTER TABLE download_jobs ADD COLUMN {name} {definition}"
                )

    @staticmethod
    def _playlist(row: sqlite3.Row) -> Playlist:
        return Playlist(
            id=row["id"],
            name=row["name"],
            cover_path=row["cover_path"],
            is_favorites=bool(row["is_favorites"]),
            created_at=row["created_at"],
            modified_at=row["modified_at"],
            source_url=row["source_url"],
            source_id=row["source_id"],
            sync_file=row["sync_file"],
            managed_dir=row["managed_dir"],
            sync_mode=row["sync_mode"],
            auto_sync=row["auto_sync"],
            cover_policy=row["cover_policy"],
            order_policy=row["order_policy"],
            sync_status=row["sync_status"],
            last_sync_at=row["last_sync_at"],
            last_sync_result=row["last_sync_result"],
        )

    def all_tracks(self, search: str = "") -> list[Track]:
        if search:
            query = """SELECT * FROM tracks WHERE title LIKE ? OR artist LIKE ? OR album LIKE ?
                       OR genre LIKE ? ORDER BY album, disc_number, track_number, title"""
            needle = f"%{search}%"
            rows = self.connection.execute(
                query, (needle, needle, needle, needle)
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM tracks ORDER BY album, disc_number, track_number, title"
            ).fetchall()
        return [self._track(row) for row in rows]

    def recent_tracks(self, limit: int = 8) -> list[Track]:
        rows = self.connection.execute(
            "SELECT * FROM tracks ORDER BY COALESCE(last_played, added_at) DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._track(row) for row in rows]

    def albums(self) -> list[dict]:
        rows = self.connection.execute(
            """SELECT album, album_artist, MAX(year) year, COUNT(*) track_count,
                      MIN(cover_path) cover_path, MIN(id) id
               FROM tracks GROUP BY album, album_artist ORDER BY album COLLATE NOCASE"""
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_tracks(self, tracks: Iterable[Track]) -> int:
        count = 0
        for track in tracks:
            self.connection.execute(
                """INSERT INTO tracks(title, artist, album, album_artist, year, genre,
                    track_number, disc_number, duration, path, cover_path, spotify_id, isrc)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(path) DO UPDATE SET title=excluded.title, artist=excluded.artist,
                    album=excluded.album, album_artist=excluded.album_artist, year=excluded.year,
                    genre=excluded.genre, track_number=excluded.track_number, disc_number=excluded.disc_number,
                    duration=excluded.duration, cover_path=excluded.cover_path,
                    spotify_id=COALESCE(excluded.spotify_id, tracks.spotify_id),
                    isrc=COALESCE(excluded.isrc, tracks.isrc)""",
                (
                    track.title,
                    track.artist,
                    track.album,
                    track.album_artist,
                    track.year,
                    track.genre,
                    track.track_number,
                    track.disc_number,
                    track.duration,
                    track.path,
                    track.cover_path,
                    track.spotify_id,
                    track.isrc,
                ),
            )
            count += 1
        self.connection.commit()
        return count

    def mark_played(self, track: Track) -> None:
        self.connection.execute(
            "UPDATE tracks SET play_count = play_count + 1, last_played = CURRENT_TIMESTAMP WHERE path = ?",
            (track.path,),
        )
        self.connection.commit()

    def remove_missing(self) -> None:
        self.connection.execute(
            "DELETE FROM tracks WHERE path NOT IN (SELECT path FROM tracks WHERE path IS NOT NULL)"
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def save_queue(self, tracks: Iterable[Track]) -> None:
        value = json.dumps([track.path for track in tracks])
        self.connection.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES('queue', ?)", (value,)
        )
        self.connection.commit()

    def save_playback(self, track: Track | None, position: float = 0.0) -> None:
        value = json.dumps(
            {
                "path": track.path if track else None,
                "position": max(0.0, float(position)),
            }
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES('playback', ?)",
            (value,),
        )
        self.connection.commit()

    def load_playback(self) -> tuple[str, float] | None:
        row = self.connection.execute(
            "SELECT value FROM settings WHERE key='playback'"
        ).fetchone()
        if not row:
            return None
        try:
            value = json.loads(row[0])
            path = value.get("path")
            if not path:
                return None
            return path, max(0.0, float(value.get("position", 0.0)))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def track_by_path(self, path: str) -> Track | None:
        row = self.connection.execute(
            "SELECT * FROM tracks WHERE path = ?", (path,)
        ).fetchone()
        return self._track(row) if row else None

    def remove_track(self, path: str) -> None:
        """Remove a track record without touching the audio file."""
        self.connection.execute("DELETE FROM tracks WHERE path = ?", (path,))
        self.connection.commit()

    def playlists(self) -> list[Playlist]:
        rows = self.connection.execute(
            "SELECT * FROM playlists ORDER BY is_favorites DESC, name COLLATE NOCASE"
        ).fetchall()
        return [self._playlist(row) for row in rows]

    def playlist(self, playlist_id: int) -> Playlist | None:
        row = self.connection.execute(
            "SELECT * FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return self._playlist(row) if row else None

    def favorites(self) -> Playlist:
        row = self.connection.execute(
            "SELECT * FROM playlists WHERE is_favorites = 1 LIMIT 1"
        ).fetchone()
        if row:
            return self._playlist(row)
        self.connection.execute(
            "INSERT INTO playlists(name, is_favorites) VALUES('Favorites', 1)"
        )
        self.connection.commit()
        return self.favorites()

    def create_playlist(
        self, name: str, cover_path: str | None = None, **source
    ) -> Playlist:
        cursor = self.connection.execute(
            """INSERT INTO playlists(
                name, cover_path, source_url, source_id, sync_file, managed_dir,
                sync_mode, auto_sync, cover_policy, order_policy, sync_status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                name.strip(),
                cover_path,
                source.get("source_url"),
                source.get("source_id"),
                source.get("sync_file"),
                source.get("managed_dir"),
                source.get("sync_mode", "safe"),
                source.get("auto_sync", "manual"),
                source.get("cover_policy", "follow"),
                source.get("order_policy", "spotify"),
                source.get("sync_status", "disconnected"),
            ),
        )
        self.connection.commit()
        return self.playlist(cursor.lastrowid)

    def playlist_by_source(
        self, source_id: str | None = None, source_url: str | None = None
    ) -> Playlist | None:
        if source_id:
            row = self.connection.execute(
                "SELECT * FROM playlists WHERE source_id = ? LIMIT 1", (source_id,)
            ).fetchone()
        elif source_url:
            row = self.connection.execute(
                "SELECT * FROM playlists WHERE source_url = ? LIMIT 1", (source_url,)
            ).fetchone()
        else:
            return None
        return self._playlist(row) if row else None

    def update_playlist_source(self, playlist_id: int, **values) -> None:
        allowed = {
            "source_url",
            "source_id",
            "sync_file",
            "managed_dir",
            "sync_mode",
            "auto_sync",
            "cover_policy",
            "order_policy",
            "sync_status",
            "last_sync_at",
            "last_sync_result",
        }
        values = {key: value for key, value in values.items() if key in allowed}
        if not values:
            return
        assignments = ", ".join(f"{key} = ?" for key in values)
        self.connection.execute(
            f"UPDATE playlists SET {assignments}, modified_at = CURRENT_TIMESTAMP WHERE id = ?",
            (*values.values(), playlist_id),
        )
        self.connection.commit()

    def track_by_spotify_id(self, spotify_id: str) -> Track | None:
        row = self.connection.execute(
            "SELECT * FROM tracks WHERE spotify_id = ? LIMIT 1", (spotify_id,)
        ).fetchone()
        if row:
            return self._track(row)
        row = self.connection.execute(
            "SELECT t.* FROM track_sources s JOIN tracks t ON t.id = s.track_id "
            "WHERE s.spotify_id = ? LIMIT 1",
            (spotify_id,),
        ).fetchone()
        return self._track(row) if row else None

    def track_by_isrc(self, isrc: str) -> Track | None:
        row = self.connection.execute(
            "SELECT * FROM tracks WHERE isrc = ? LIMIT 1", (isrc,)
        ).fetchone()
        return self._track(row) if row else None

    def track_by_metadata(self, track: Track) -> Track | None:
        row = self.connection.execute(
            """SELECT * FROM tracks WHERE title = ? AND artist = ? AND album = ?
               AND ABS(duration - ?) < 2 LIMIT 1""",
            (track.title, track.artist, track.album, track.duration),
        ).fetchone()
        return self._track(row) if row else None

    def save_track_source(
        self, spotify_id: str, track: Track, isrc: str | None = None
    ) -> None:
        stored = self.track_by_path(track.path)
        if stored and stored.id is not None:
            self.connection.execute(
                "UPDATE tracks SET spotify_id = COALESCE(?, spotify_id), isrc = COALESCE(?, isrc) WHERE id = ?",
                (spotify_id, isrc, stored.id),
            )
            track.id = stored.id
        self.connection.execute(
            """INSERT INTO track_sources(spotify_id, isrc, track_id, local_path)
               VALUES(?,?,?,?)
               ON CONFLICT(spotify_id) DO UPDATE SET isrc=excluded.isrc,
               track_id=excluded.track_id, local_path=excluded.local_path""",
            (spotify_id, isrc, stored.id if stored else track.id, track.path),
        )
        self.connection.commit()

    def save_download_job(
        self,
        job_id: str,
        job_type: str,
        source: str,
        state: str,
        progress: float = 0.0,
        destination: str | None = None,
        error: str | None = None,
        completed_at: str | None = None,
        lyrics_mode: str = "none",
        lyrics_fallback: bool = True,
        generate_lrc: bool = False,
        lyrics_providers: str | None = None,
        sync_remove_lrc: bool = False,
    ) -> None:
        self.connection.execute(
            """INSERT INTO download_jobs(id, job_type, source, state, progress, destination, error, completed_at,
               lyrics_mode, lyrics_fallback, generate_lrc, lyrics_providers, sync_remove_lrc)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,
               progress=excluded.progress, destination=excluded.destination, error=excluded.error,
               completed_at=excluded.completed_at, lyrics_mode=excluded.lyrics_mode,
               lyrics_fallback=excluded.lyrics_fallback, generate_lrc=excluded.generate_lrc,
               lyrics_providers=excluded.lyrics_providers, sync_remove_lrc=excluded.sync_remove_lrc""",
            (
                job_id,
                job_type,
                source,
                state,
                progress,
                destination,
                error,
                completed_at,
                lyrics_mode,
                int(lyrics_fallback),
                int(generate_lrc),
                lyrics_providers,
                int(sync_remove_lrc),
            ),
        )
        self.connection.commit()

    def download_jobs(self, limit: int = 50) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM download_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def lyrics_for_track(self, track_id: int | None) -> list[dict]:
        if track_id is None:
            return []
        rows = self.connection.execute(
            "SELECT * FROM lyrics WHERE track_id = ? ORDER BY user_edited DESC, modified_at DESC",
            (track_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_lyrics(
        self,
        track_id: int,
        kind: str,
        file_path: str | None,
        provider: str | None,
        language: str | None,
        content: str,
        *,
        user_edited: bool = False,
        timing_offset_ms: int = 0,
        checksum: str | None = None,
        source_id: str | None = None,
    ) -> int:
        row = self.connection.execute(
            "SELECT id FROM lyrics WHERE track_id = ? AND kind = ? AND file_path IS ?",
            (track_id, kind, file_path),
        ).fetchone()
        if row:
            self.connection.execute(
                """UPDATE lyrics SET provider=?, language=?, source_id=?, content=?,
                   modified_at=CURRENT_TIMESTAMP, user_edited=?, timing_offset_ms=?, checksum=?
                   WHERE id=?""",
                (
                    provider,
                    language,
                    source_id,
                    content,
                    int(user_edited),
                    timing_offset_ms,
                    checksum,
                    row[0],
                ),
            )
            lyric_id = row[0]
        else:
            cursor = self.connection.execute(
                """INSERT INTO lyrics(track_id, kind, file_path, provider, language, source_id,
                   content, user_edited, timing_offset_ms, checksum)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    track_id,
                    kind,
                    file_path,
                    provider,
                    language,
                    source_id,
                    content,
                    int(user_edited),
                    timing_offset_ms,
                    checksum,
                ),
            )
            lyric_id = cursor.lastrowid
        self.connection.commit()
        return lyric_id

    def delete_lyrics(self, lyric_id: int) -> None:
        self.connection.execute("DELETE FROM lyrics WHERE id = ?", (lyric_id,))
        self.connection.commit()

    def lyrics_path_references(self, file_path: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) FROM lyrics WHERE file_path = ?", (file_path,)
        ).fetchone()
        return int(row[0]) if row else 0

    def update_lyrics_offset(self, lyric_id: int, offset_ms: int) -> None:
        self.connection.execute(
            "UPDATE lyrics SET timing_offset_ms = ?, modified_at = CURRENT_TIMESTAMP WHERE id = ?",
            (int(offset_ms), lyric_id),
        )
        self.connection.commit()

    def lyrics_coverage(self, track_ids: Iterable[int] | None = None) -> dict[str, int]:
        if track_ids:
            values = list(track_ids)
            placeholders = ",".join("?" for _ in values)
            rows = self.connection.execute(
                f"SELECT kind, COUNT(DISTINCT track_id) count FROM lyrics WHERE track_id IN ({placeholders}) GROUP BY kind",
                values,
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT kind, COUNT(DISTINCT track_id) count FROM lyrics GROUP BY kind"
            ).fetchall()
        return {row["kind"]: row["count"] for row in rows}

    def rename_playlist(self, playlist_id: int, name: str) -> None:
        self.connection.execute(
            "UPDATE playlists SET name = ?, modified_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND is_favorites = 0",
            (name.strip(), playlist_id),
        )
        self.connection.commit()

    def update_playlist_cover(self, playlist_id: int, cover_path: str | None) -> None:
        self.connection.execute(
            "UPDATE playlists SET cover_path = ?, modified_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (cover_path, playlist_id),
        )
        self.connection.commit()

    def delete_playlist(self, playlist_id: int) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM playlists WHERE id = ? AND is_favorites = 0", (playlist_id,)
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def playlist_tracks(
        self, playlist_id: int, search: str = "", sort: str = "custom"
    ) -> list[Track]:
        order = {
            "custom": "pt.position",
            "title": "t.title COLLATE NOCASE, pt.position",
            "artist": "t.artist COLLATE NOCASE, t.title COLLATE NOCASE",
            "album": "t.album COLLATE NOCASE, t.disc_number, t.track_number, t.title",
            "duration": "t.duration, t.title COLLATE NOCASE",
            "date": "pt.added_at, pt.position",
        }.get(sort, "pt.position")
        query = (
            "SELECT t.* FROM playlist_tracks pt JOIN tracks t ON t.id = pt.track_id "
            "WHERE pt.playlist_id = ?"
        )
        params: list[object] = [playlist_id]
        if search:
            query += " AND (t.title LIKE ? OR t.artist LIKE ? OR t.album LIKE ?)"
            needle = f"%{search}%"
            params.extend((needle, needle, needle))
        query += f" ORDER BY {order}"
        rows = self.connection.execute(query, params).fetchall()
        return [self._track(row) for row in rows]

    def playlist_track_ids(self, playlist_id: int) -> list[int]:
        rows = self.connection.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (playlist_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def add_tracks_to_playlist(self, playlist_id: int, tracks: Iterable[Track]) -> int:
        added = 0
        row = self.connection.execute(
            "SELECT COALESCE(MAX(position), -1) FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        position = row[0] + 1
        for track in tracks:
            if track.id is None:
                stored = self.track_by_path(track.path)
                track_id = stored.id if stored else None
            else:
                track_id = track.id
            if track_id is None:
                continue
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO playlist_tracks(playlist_id, track_id, position) VALUES(?,?,?)",
                (playlist_id, track_id, position),
            )
            if cursor.rowcount:
                added += 1
                position += 1
        self.connection.execute(
            "UPDATE playlists SET modified_at = CURRENT_TIMESTAMP WHERE id = ?",
            (playlist_id,),
        )
        self.connection.commit()
        return added

    def is_favorite(self, track: Track) -> bool:
        stored = track if track.id is not None else self.track_by_path(track.path)
        if not stored or stored.id is None:
            return False
        row = self.connection.execute(
            "SELECT 1 FROM playlist_tracks pt JOIN playlists p ON p.id = pt.playlist_id "
            "WHERE p.is_favorites = 1 AND pt.track_id = ?",
            (stored.id,),
        ).fetchone()
        return row is not None

    def set_favorite(self, track: Track, favorite: bool) -> None:
        favorites = self.favorites()
        if favorite:
            self.add_tracks_to_playlist(favorites.id, [track])
        else:
            stored = track if track.id is not None else self.track_by_path(track.path)
            if stored and stored.id is not None:
                self.remove_track_from_playlist(favorites.id, stored.id)

    def remove_track_from_playlist(self, playlist_id: int, track_id: int) -> None:
        self.connection.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
            (playlist_id, track_id),
        )
        self._normalize_playlist_positions(playlist_id)
        self.connection.commit()

    def clear_playlist(self, playlist_id: int) -> None:
        self.connection.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,)
        )
        self.connection.execute(
            "UPDATE playlists SET modified_at = CURRENT_TIMESTAMP WHERE id = ?",
            (playlist_id,),
        )
        self.connection.commit()

    def reorder_playlist(self, playlist_id: int, ordered_track_ids: list[int]) -> None:
        with self.connection:
            for position, track_id in enumerate(ordered_track_ids):
                self.connection.execute(
                    "UPDATE playlist_tracks SET position = ? WHERE playlist_id = ? AND track_id = ?",
                    (position, playlist_id, track_id),
                )
            self.connection.execute(
                "UPDATE playlists SET modified_at = CURRENT_TIMESTAMP WHERE id = ?",
                (playlist_id,),
            )

    def _normalize_playlist_positions(self, playlist_id: int) -> None:
        rows = self.connection.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (playlist_id,),
        ).fetchall()
        for position, row in enumerate(rows):
            self.connection.execute(
                "UPDATE playlist_tracks SET position = ? WHERE playlist_id = ? AND track_id = ?",
                (position, playlist_id, row[0]),
            )

    def load_queue(self) -> list[Track]:
        row = self.connection.execute(
            "SELECT value FROM settings WHERE key='queue'"
        ).fetchone()
        if not row:
            return []
        paths = json.loads(row[0])
        tracks = {track.path: track for track in self.all_tracks()}
        return [tracks[path] for path in paths if path in tracks]
