# __init__.py
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

from .database import LibraryDatabase
from .grouping import (
    AlbumGroup,
    ArtistGroup,
    group_albums,
    group_artists,
    normalize_group_name,
    parse_artists,
)


def __getattr__(name):
    # Keep pure grouping/database imports usable in non-GTK tooling and tests.
    # The scanner initializes GStreamer, so load it only when the application
    # actually asks for it.
    if name == "LibraryScanner":
        from .scanner import LibraryScanner

        return LibraryScanner
    raise AttributeError(name)


__all__ = [
    "AlbumGroup",
    "ArtistGroup",
    "LibraryDatabase",
    "LibraryScanner",
    "group_albums",
    "group_artists",
    "normalize_group_name",
    "parse_artists",
]
