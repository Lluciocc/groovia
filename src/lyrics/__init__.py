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

from .parser import LyricsLine, LyricsTimeline, LyricsWord, parse_lrc, parse_lyrics, parse_ttml
from .service import LyricsBundle, LyricsEnrichment, LyricsService

__all__ = [
    "LyricsLine",
    "LyricsTimeline",
    "LyricsWord",
    "LyricsBundle",
    "LyricsEnrichment",
    "LyricsService",
    "parse_lyrics",
    "parse_lrc",
    "parse_ttml",
]
