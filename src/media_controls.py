from __future__ import annotations

from typing import Protocol

from .platform_compat import media_backend_name


class MediaControlBackend(Protocol):
    """Minimal lifecycle shared by current and future desktop media backends."""

    def close(self) -> None: ...


def create_media_control_backend(window) -> MediaControlBackend | None:
    """Create the supported platform backend without importing it elsewhere."""
    if media_backend_name() == "mpris":
        from .mpris import MprisService

        return MprisService(window)
    # A future official macOS media backend belongs behind this boundary.
    return None
