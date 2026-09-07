import sqlite3
from pathlib import Path

from src.library.database import LibraryDatabase
from src.models import Track


def track(path: str) -> Track:
    return Track(None, "Song", "Artist", "Album", "Artist", "", "House", 1, 1, 180, path)


def test_empty_database_migrates_play_events(tmp_path):
    database = LibraryDatabase(str(tmp_path))
    tables = {
        row[0]
        for row in database.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "play_events" in tables
    database.close()


def test_existing_database_gets_additive_play_event_migration(tmp_path):
    database_path = Path(tmp_path) / "groovia" / "library.db"
    database_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(database_path)
    connection.executescript("""
        CREATE TABLE tracks (
          id INTEGER PRIMARY KEY, title TEXT NOT NULL, artist TEXT NOT NULL,
          album TEXT NOT NULL, album_artist TEXT NOT NULL, year TEXT NOT NULL,
          genre TEXT NOT NULL, track_number INTEGER NOT NULL DEFAULT 0,
          disc_number INTEGER NOT NULL DEFAULT 1, duration REAL NOT NULL DEFAULT 0,
          path TEXT NOT NULL UNIQUE, cover_path TEXT,
          added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE playlists (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, cover_path TEXT,
          is_favorites INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          modified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE playlist_tracks (
          playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
          track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
          position INTEGER NOT NULL, added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (playlist_id, track_id)
        );
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    connection.commit()
    connection.close()

    database = LibraryDatabase(str(tmp_path))
    assert database.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='play_events'"
    ).fetchone()
    columns = {
        row[1] for row in database.connection.execute("PRAGMA table_info(tracks)").fetchall()
    }
    assert {"play_count", "last_played"} <= columns
    database.close()


def test_mark_played_updates_aggregates_and_appends_one_event(tmp_path):
    database = LibraryDatabase(str(tmp_path))
    original = track(str(tmp_path / "song.mp3"))
    database.upsert_tracks([original])
    stored = database.track_by_path(original.path)
    database.mark_played(stored, "library-automatic")

    refreshed = database.track_by_path(original.path)
    events = database.connection.execute("SELECT track_id, context FROM play_events").fetchall()
    assert refreshed.play_count == 1
    assert refreshed.last_played is not None
    assert len(events) == 1
    assert events[0][0] == stored.id
    assert events[0][1] == "library-automatic"
    database.close()
