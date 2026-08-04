import os
import json
import sqlite3
from pathlib import Path
from typing import Iterable

from ..models import Playlist, Track


class LibraryDatabase:
    """Small SQLite store for the local library and playback history."""

    def __init__(self, data_dir: str | None = None):
        base = Path(data_dir or os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        self.path = base / "groovia" / "library.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(
            """
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
            """
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO playlists(name, is_favorites) VALUES('Favorites', 1)"
        )
        self.connection.commit()

    def _track(self, row: sqlite3.Row) -> Track:
        return Track(**{key: row[key] for key in Track.__dataclass_fields__})

    @staticmethod
    def _playlist(row: sqlite3.Row) -> Playlist:
        return Playlist(
            id=row["id"], name=row["name"], cover_path=row["cover_path"],
            is_favorites=bool(row["is_favorites"]), created_at=row["created_at"],
            modified_at=row["modified_at"],
        )

    def all_tracks(self, search: str = "") -> list[Track]:
        if search:
            query = """SELECT * FROM tracks WHERE title LIKE ? OR artist LIKE ? OR album LIKE ?
                       OR genre LIKE ? ORDER BY album, disc_number, track_number, title"""
            needle = f"%{search}%"
            rows = self.connection.execute(query, (needle, needle, needle, needle)).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM tracks ORDER BY album, disc_number, track_number, title"
            ).fetchall()
        return [self._track(row) for row in rows]

    def recent_tracks(self, limit: int = 8) -> list[Track]:
        rows = self.connection.execute(
            "SELECT * FROM tracks ORDER BY COALESCE(last_played, added_at) DESC LIMIT ?", (limit,)
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
                    track_number, disc_number, duration, path, cover_path)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(path) DO UPDATE SET title=excluded.title, artist=excluded.artist,
                    album=excluded.album, album_artist=excluded.album_artist, year=excluded.year,
                    genre=excluded.genre, track_number=excluded.track_number, disc_number=excluded.disc_number,
                    duration=excluded.duration, cover_path=excluded.cover_path""",
                (track.title, track.artist, track.album, track.album_artist, track.year, track.genre,
                 track.track_number, track.disc_number, track.duration, track.path, track.cover_path),
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
        self.connection.execute("DELETE FROM tracks WHERE path NOT IN (SELECT path FROM tracks WHERE path IS NOT NULL)")
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def save_queue(self, tracks: Iterable[Track]) -> None:
        value = json.dumps([track.path for track in tracks])
        self.connection.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('queue', ?)", (value,))
        self.connection.commit()

    def save_playback(self, track: Track | None, position: float = 0.0) -> None:
        value = json.dumps({
            "path": track.path if track else None,
            "position": max(0.0, float(position)),
        })
        self.connection.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES('playback', ?)",
            (value,),
        )
        self.connection.commit()

    def load_playback(self) -> tuple[str, float] | None:
        row = self.connection.execute("SELECT value FROM settings WHERE key='playback'").fetchone()
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
        row = self.connection.execute("SELECT * FROM tracks WHERE path = ?", (path,)).fetchone()
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
        self.connection.execute("INSERT INTO playlists(name, is_favorites) VALUES('Favorites', 1)")
        self.connection.commit()
        return self.favorites()

    def create_playlist(self, name: str, cover_path: str | None = None) -> Playlist:
        cursor = self.connection.execute(
            "INSERT INTO playlists(name, cover_path) VALUES(?, ?)", (name.strip(), cover_path)
        )
        self.connection.commit()
        return self.playlist(cursor.lastrowid)

    def rename_playlist(self, playlist_id: int, name: str) -> None:
        self.connection.execute(
            "UPDATE playlists SET name = ?, modified_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND is_favorites = 0", (name.strip(), playlist_id)
        )
        self.connection.commit()

    def update_playlist_cover(self, playlist_id: int, cover_path: str | None) -> None:
        self.connection.execute(
            "UPDATE playlists SET cover_path = ?, modified_at = CURRENT_TIMESTAMP "
            "WHERE id = ?", (cover_path, playlist_id)
        )
        self.connection.commit()

    def delete_playlist(self, playlist_id: int) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM playlists WHERE id = ? AND is_favorites = 0", (playlist_id,)
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def playlist_tracks(self, playlist_id: int, search: str = "", sort: str = "custom") -> list[Track]:
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
            "UPDATE playlists SET modified_at = CURRENT_TIMESTAMP WHERE id = ?", (playlist_id,)
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
        self.connection.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        self.connection.execute(
            "UPDATE playlists SET modified_at = CURRENT_TIMESTAMP WHERE id = ?", (playlist_id,)
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
                "UPDATE playlists SET modified_at = CURRENT_TIMESTAMP WHERE id = ?", (playlist_id,)
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
        row = self.connection.execute("SELECT value FROM settings WHERE key='queue'").fetchone()
        if not row:
            return []
        paths = json.loads(row[0])
        tracks = {track.path: track for track in self.all_tracks()}
        return [tracks[path] for path in paths if path in tracks]
