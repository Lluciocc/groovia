"""Tolerant parser for synchronized LRC and plain-text lyrics."""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field


TIMESTAMP = re.compile(r"\[(?P<minutes>\d+):(?P<seconds>\d{1,2})(?:[.:](?P<fraction>\d{1,3}))?\]")
TAG = re.compile(r"\[(?P<name>[A-Za-z][\w-]*):(?P<value>[^\]]*)\]")


def _fraction_ms(value: str | None) -> int:
    if not value:
        return 0
    # .1, .12 and .123 mean tenths, hundredths and milliseconds.
    return int((value + "000")[:3])


@dataclass(slots=True)
class LyricsLine:
    start_time_ms: int
    end_time_ms: int | None
    text: str
    words: list[tuple[int, int | None, str]] = field(default_factory=list)


@dataclass(slots=True)
class LyricsTimeline:
    lines: list[LyricsLine] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    offset_ms: int = 0
    synchronized: bool = False
    provider: str | None = None
    language: str | None = None
    file_path: str | None = None
    user_edited: bool = False

    def effective_starts(self) -> list[int]:
        return [line.start_time_ms + self.offset_ms for line in self.lines]

    def current_index(self, position_ms: int) -> int:
        if not self.lines:
            return -1
        starts = self.effective_starts()
        index = bisect.bisect_right(starts, max(0, int(position_ms))) - 1
        return max(0, min(index, len(self.lines) - 1))


def _timestamp_ms(match: re.Match) -> int:
    return int(match.group("minutes")) * 60_000 + int(match.group("seconds")) * 1_000 + _fraction_ms(match.group("fraction"))


def parse_lrc(content: str, *, provider: str | None = None, file_path: str | None = None) -> LyricsTimeline:
    metadata: dict[str, str] = {}
    timed: list[tuple[int, str]] = []
    offset = 0
    for raw_line in content.splitlines():
        tags = list(TAG.finditer(raw_line))
        timestamps = list(TIMESTAMP.finditer(raw_line))
        for tag in tags:
            name = tag.group("name").lower()
            value = tag.group("value").strip()
            metadata[name] = value
            if name == "offset":
                try:
                    offset = int(float(value))
                except ValueError:
                    pass
        if not timestamps:
            continue
        text = TIMESTAMP.sub("", raw_line).strip()
        for timestamp in timestamps:
            timed.append((_timestamp_ms(timestamp), text))
    timed.sort(key=lambda item: item[0])
    lines = [
        LyricsLine(start, timed[index + 1][0] if index + 1 < len(timed) else None, text)
        for index, (start, text) in enumerate(timed)
    ]
    return LyricsTimeline(
        lines=lines,
        metadata=metadata,
        offset_ms=offset,
        synchronized=bool(lines),
        provider=provider,
        language=metadata.get("language"),
        file_path=file_path,
    )


def parse_lyrics(content: str, *, file_path: str | None = None, provider: str | None = None) -> LyricsTimeline:
    """Parse LRC when timestamps exist, otherwise preserve plain lyrics."""
    timeline = parse_lrc(content, provider=provider, file_path=file_path)
    if timeline.lines:
        return timeline
    return LyricsTimeline(
        metadata={},
        synchronized=False,
        provider=provider,
        file_path=file_path,
        lines=[LyricsLine(0, None, line) for line in content.splitlines()],
    )
