from __future__ import annotations

TEMPO_FILTER_CANDIDATES = ("rubberband", "pitch", "scaletempo")


def select_tempo_filter(factory_find) -> str | None:
    """Choose a pitch-preserving tempo element, or keep normal playback."""
    return next((name for name in TEMPO_FILTER_CANDIDATES if factory_find(name)), None)
