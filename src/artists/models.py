# models.py
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

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ArtistMetadata:
    normalized_name: str
    display_name: str
    provider: str = "theaudiodb"
    provider_artist_id: str | None = None
    provider_url: str | None = None
    alternate_name: str | None = None
    biography: str | None = None
    country: str | None = None
    country_code: str | None = None
    formed_year: str | None = None
    born_year: str | None = None
    died_year: str | None = None
    disbanded: str | None = None
    genre: str | None = None
    style: str | None = None
    mood: str | None = None
    label: str | None = None
    website: str | None = None
    facebook: str | None = None
    twitter: str | None = None
    image_url: str | None = None
    image_path: str | None = None
    wide_thumb_url: str | None = None
    fanart_url: str | None = None
    fanart2_url: str | None = None
    fanart3_url: str | None = None
    fanart4_url: str | None = None
    banner_url: str | None = None
    logo_url: str | None = None
    cutout_url: str | None = None
    clearart_url: str | None = None
    raw_json: str | None = None
    not_found: bool = False
    last_checked: float = 0.0

    @property
    def artwork_available(self) -> bool:
        try:
            return bool(self.image_path and Path(self.image_path).is_file())
        except (OSError, TypeError, ValueError):
            return False

    @property
    def has_information(self) -> bool:
        return bool(
            self.artwork_available
            or self.biography
            or self.country
            or self.genre
            or self.style
            or self.formed_year
            or self.born_year
            or self.provider_artist_id
        )
