# artists/__init__.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .info import (
    THEAUDIODB_FREE_LIMIT,
    THEAUDIODB_REQUEST_BUDGET,
    THEAUDIODB_WINDOW_SECONDS,
    ArtistInfoService,
    SlidingWindowRateLimiter,
)
from .models import ArtistMetadata
from .theaudiodb import (
    ArtistLookup,
    TheAudioDBArtistProvider,
    TheAudioDBError,
    TheAudioDBRateLimited,
    find_exact_artist_match,
    normalize_artist_name,
)

__all__ = [
    "THEAUDIODB_FREE_LIMIT",
    "THEAUDIODB_REQUEST_BUDGET",
    "THEAUDIODB_WINDOW_SECONDS",
    "ArtistInfoService",
    "ArtistLookup",
    "ArtistMetadata",
    "SlidingWindowRateLimiter",
    "TheAudioDBArtistProvider",
    "TheAudioDBError",
    "TheAudioDBRateLimited",
    "find_exact_artist_match",
    "normalize_artist_name",
]
