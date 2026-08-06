"""Tolerant parser and canonical model for plain, line and word-synced lyrics."""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field


TIMESTAMP = re.compile(
    r"\[(?P<minutes>\d+):(?P<seconds>\d{1,2})(?:[.:](?P<fraction>\d{1,3}))?\]"
)
WORD_TIMESTAMP = re.compile(
    r"<(?P<minutes>\d+):(?P<seconds>\d{1,2})(?:[.:](?P<fraction>\d{1,3}))?>"
)
TAG = re.compile(r"\[(?P<name>[A-Za-z][\w-]*):(?P<value>[^\]]*)\]")


def _fraction_ms(value: str | None) -> int:
    if not value:
        return 0
    return int((value + "000")[:3])


@dataclass(slots=True)
class LyricsWord:
    text: str
    start_time_ms: int
    end_time_ms: int | None = None


@dataclass(slots=True)
class LyricsLine:
    start_time_ms: int
    end_time_ms: int | None
    text: str
    words: list[LyricsWord] = field(default_factory=list)

    @property
    def word_synchronized(self) -> bool:
        return bool(self.words)


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
    _line_starts: list[int] = field(default_factory=list, repr=False)
    _word_starts: list[list[int]] = field(default_factory=list, repr=False)

    def __post_init__(self):
        self._line_starts = [line.start_time_ms + self.offset_ms for line in self.lines]
        self._word_starts = [
            [word.start_time_ms + self.offset_ms for word in line.words]
            for line in self.lines
        ]

    @property
    def word_synchronized(self) -> bool:
        return any(line.words for line in self.lines)

    @property
    def synchronized_quality(self) -> str:
        if self.word_synchronized:
            return "word"
        if self.synchronized:
            return "line"
        return "plain"

    def effective_starts(self) -> list[int]:
        return self._line_starts

    def apply_offset(self, amount_ms: int):
        self.offset_ms = int(amount_ms)
        self._line_starts = [line.start_time_ms + self.offset_ms for line in self.lines]
        self._word_starts = [
            [word.start_time_ms + self.offset_ms for word in line.words]
            for line in self.lines
        ]

    def current_index(self, position_ms: int) -> int:
        if not self.lines:
            return -1
        index = bisect.bisect_right(self._line_starts, max(0, int(position_ms))) - 1
        return max(0, min(index, len(self.lines) - 1))

    def current_word_index(self, line_index: int, position_ms: int) -> int:
        if not 0 <= line_index < len(self.lines):
            return -1
        words = self.lines[line_index].words
        if not words:
            return -1
        starts = self._word_starts[line_index]
        index = bisect.bisect_right(starts, max(0, int(position_ms))) - 1
        return max(-1, min(index, len(words) - 1))


def _timestamp_ms(match: re.Match) -> int:
    return (
        int(match.group("minutes")) * 60_000
        + int(match.group("seconds")) * 1_000
        + _fraction_ms(match.group("fraction"))
    )


def _parse_word_segments(text: str) -> tuple[str, list[tuple[int, str]]]:
    matches = list(WORD_TIMESTAMP.finditer(text))
    if not matches:
        return text.strip(), []
    words: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        word = text[match.end():end].strip()
        if word:
            words.append((_timestamp_ms(match), word))
    plain = WORD_TIMESTAMP.sub("", text).strip()
    if not plain:
        plain = " ".join(word for _, word in words)
    return plain, words


def parse_lrc(content: str, *, provider: str | None = None, file_path: str | None = None) -> LyricsTimeline:
    metadata: dict[str, str] = {}
    timed: list[tuple[int, str, list[tuple[int, str]]]] = []
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
        plain, words = _parse_word_segments(text)
        for timestamp in timestamps:
            timed.append((_timestamp_ms(timestamp), plain, words))

    timed.sort(key=lambda item: item[0])
    lines: list[LyricsLine] = []
    for index, (start, text, word_specs) in enumerate(timed):
        end = timed[index + 1][0] if index + 1 < len(timed) else None
        words = [
            LyricsWord(
                text=word,
                start_time_ms=word_start,
                end_time_ms=(word_specs[word_index + 1][0] if word_index + 1 < len(word_specs) else end),
            )
            for word_index, (word_start, word) in enumerate(word_specs)
        ]
        lines.append(LyricsLine(start, end, text, words))
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
