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

    @property
    def subtitle(self) -> str:
        return f"{self.artist} · {self.album}"

    @property
    def duration_label(self) -> str:
        seconds = max(0, int(self.duration))
        return f"{seconds // 60}:{seconds % 60:02d}"
