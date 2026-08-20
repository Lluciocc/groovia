# grouping.py
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

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

from ..models import Track


def normalize_group_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    return " ".join(text.split()).casefold()


def parse_artists(value: str | None) -> list[str]:
    artists: list[str] = []
    seen: set[str] = set()
    for part in (value or "").split("/"):
        artist = " ".join(part.split())
        key = normalize_group_name(artist)
        if not key or key in seen:
            continue
        seen.add(key)
        artists.append(artist)
    return artists


def _positive_number(value: int | None, fallback: int) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def album_track_sort_key(track: Track) -> tuple:
    disc = _positive_number(track.disc_number, 1)
    number = _positive_number(track.track_number, 1_000_000)
    return (
        disc,
        number,
        normalize_group_name(track.title),
        normalize_group_name(track.path),
    )


def _year_sort_value(year: str | None) -> int:
    match = re.search(r"\d{4}", year or "")
    return int(match.group()) if match else 9999


def artist_track_sort_key(track: Track) -> tuple:
    return (
        _year_sort_value(track.year),
        normalize_group_name(track.album),
        *album_track_sort_key(track),
    )


def _track_album_artist(track: Track) -> str:
    album_artist = " ".join((track.album_artist or "").split())
    if album_artist:
        return album_artist
    artists = parse_artists(track.artist)
    return artists[0] if artists else "Unknown Artist"


@dataclass(slots=True)
class AlbumGroup:
    key: tuple[str, str]
    title: str
    album_artist: str
    tracks: list[Track] = field(default_factory=list)
    cover_path: str | None = None
    year: str = ""
    play_count: int = 0
    duration: float = 0.0
    artist_names: tuple[str, ...] = ()

    @property
    def track_count(self) -> int:
        return len(self.tracks)


@dataclass(slots=True)
class ArtistGroup:
    key: str
    name: str
    tracks: list[Track] = field(default_factory=list)
    albums: list[AlbumGroup] = field(default_factory=list)
    play_count: int = 0
    image_path: str | None = None

    @property
    def track_count(self) -> int:
        return len(self.tracks)

    @property
    def album_count(self) -> int:
        return len(self.albums)


def group_albums(tracks: Iterable[Track]) -> list[AlbumGroup]:
    grouped: dict[tuple[str, str], AlbumGroup] = {}
    for track in tracks:
        title = " ".join((track.album or "").split()) or "Unknown Album"
        album_artist = _track_album_artist(track)
        key = (normalize_group_name(title), normalize_group_name(album_artist))
        album = grouped.get(key)
        if album is None:
            album = AlbumGroup(key=key, title=title, album_artist=album_artist)
            grouped[key] = album
        album.tracks.append(track)
        album.play_count += max(0, int(track.play_count or 0))
        album.duration += max(0.0, float(track.duration or 0.0))
        if not album.cover_path and track.cover_path:
            album.cover_path = track.cover_path
        if not album.year and track.year:
            album.year = track.year

    for album in grouped.values():
        album.tracks.sort(key=album_track_sort_key)
        artist_names: list[str] = []
        seen: set[str] = set()
        for track in album.tracks:
            for name in parse_artists(track.artist) or [_track_album_artist(track)]:
                key = normalize_group_name(name)
                if key not in seen:
                    seen.add(key)
                    artist_names.append(name)
        album.artist_names = tuple(artist_names)

    return sorted(
        grouped.values(),
        key=lambda album: (
            normalize_group_name(album.title),
            normalize_group_name(album.album_artist),
        ),
    )


def group_artists(
    tracks: Iterable[Track], albums: Iterable[AlbumGroup] | None = None
) -> list[ArtistGroup]:
    track_list = list(tracks)
    album_list = list(albums) if albums is not None else group_albums(track_list)
    grouped: dict[str, ArtistGroup] = {}
    albums_by_key = {album.key: album for album in album_list}
    album_key_by_path = {track.path: album.key for album in album_list for track in album.tracks}

    for track in track_list:
        names = parse_artists(track.artist)
        if not names:
            names = parse_artists(track.album_artist) or ["Unknown Artist"]
        for name in names:
            key = normalize_group_name(name)
            artist = grouped.get(key)
            if artist is None:
                artist = ArtistGroup(key=key, name=name)
                grouped[key] = artist
            artist.tracks.append(track)
            artist.play_count += max(0, int(track.play_count or 0))

    for artist in grouped.values():
        artist.tracks.sort(key=artist_track_sort_key)
        album_keys = {
            album_key_by_path[track.path]
            for track in artist.tracks
            if track.path in album_key_by_path
        }
        artist.albums = sorted(
            (albums_by_key[key] for key in album_keys),
            key=lambda album: (
                _year_sort_value(album.year),
                normalize_group_name(album.title),
                normalize_group_name(album.album_artist),
            ),
        )

    return sorted(grouped.values(), key=lambda artist: normalize_group_name(artist.name))
