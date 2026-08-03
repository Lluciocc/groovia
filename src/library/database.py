import os
import json
import sqlite3
from pathlib import Path
from typing import Iterable

from ..models import Track


class LibraryDatabase:
    """Small SQLite store for the local library and playback history."""

    def __init__(self, data_dir: str | None = None):
        base = Path(data_dir or os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        self.path = base / "groovia" / "library.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
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
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        self.connection.commit()

    def _track(self, row: sqlite3.Row) -> Track:
        return Track(**{key: row[key] for key in Track.__dataclass_fields__})

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

    def load_queue(self) -> list[Track]:
        row = self.connection.execute("SELECT value FROM settings WHERE key='queue'").fetchone()
        if not row:
            return []
        paths = json.loads(row[0])
        tracks = {track.path: track for track in self.all_tracks()}
        return [tracks[path] for path in paths if path in tracks]
