from dataclasses import dataclass


@dataclass(slots=True)
class Track:
    id: int | None
    title: str
    artist: str
    album: str
    album_artist: str
    year: str
    genre: str
    track_number: int
    disc_number: int
    duration: float
    path: str
    cover_path: str | None = None
    play_count: int = 0
    spotify_id: str | None = None
    isrc: str | None = None

    @property
    def subtitle(self) -> str:
        return f"{self.artist} · {self.album}"

    @property
    def duration_label(self) -> str:
        seconds = max(0, int(self.duration))
        return f"{seconds // 60}:{seconds % 60:02d}"


@dataclass(slots=True)
class Playlist:
    id: int
    name: str
    cover_path: str | None
    is_favorites: bool
    created_at: str
    modified_at: str
    source_url: str | None = None
    source_id: str | None = None
    sync_file: str | None = None
    managed_dir: str | None = None
    sync_mode: str = "safe"
    auto_sync: str = "manual"
    cover_policy: str = "follow"
    order_policy: str = "spotify"
    sync_status: str = "disconnected"
    last_sync_at: str | None = None
    last_sync_result: str | None = None
