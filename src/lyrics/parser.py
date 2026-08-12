# parser.py
#
# Copyright 2026 Lluciocc (llucio.cc00@gmail.com)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Lyrics document parsing and immutable-ish playback timelines.

TTML is deliberately parsed as TTML here.  In particular, Better Lyrics
documents must never make a round trip through LRC: the whitespace in and
between ``span`` elements is part of the source document's meaning.
"""

from __future__ import annotations

import bisect
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

TIMESTAMP = re.compile(r"\[(?P<minutes>\d+):(?P<seconds>\d{1,2})(?:[.:](?P<fraction>\d{1,3}))?\]")
WORD_TIMESTAMP = re.compile(
    r"<(?P<minutes>\d+):(?P<seconds>\d{1,2})(?:[.:](?P<fraction>\d{1,3}))?>"
)
TAG = re.compile(r"\[(?P<name>[A-Za-z][\w-]*):(?P<value>[^\]]*)\]")
RTL_LANGUAGES = {"ar", "dv", "fa", "he", "ku", "ps", "sd", "ug", "ur", "yi"}

TTML_NS = "http://www.w3.org/ns/ttml"
TTM_NS = "http://www.w3.org/ns/ttml#metadata"
ITUNES_NS = "http://music.apple.com/lyric-ttml-internal"
XML_NS = "http://www.w3.org/XML/1998/namespace"


class LyricsParseError(ValueError):
    """Raised when a lyrics document is recognized but cannot be parsed."""


def _fraction_ms(value: str | None) -> int:
    if not value:
        return 0
    return int((value + "000")[:3])


@dataclass(slots=True)
class LyricsWord:
    text: str
    start_time_ms: int
    end_time_ms: int | None = None
    background_vocal: bool = False
    syllable_group: str | None = None


@dataclass(slots=True)
class LyricsLine:
    start_time_ms: int
    end_time_ms: int | None
    text: str
    words: list[LyricsWord] = field(default_factory=list)
    speaker_agent: str | None = None
    speaker_name: str | None = None
    line_id: str | None = None
    background_vocals: list[LyricsWord] = field(default_factory=list)
    transliteration: str | None = None

    @property
    def word_synchronized(self) -> bool:
        return bool(self.words)


@dataclass(slots=True)
class LyricsTimeline:
    lines: list[LyricsLine] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    offset_ms: int = 0
    synchronized: bool = False
    provider: str | None = None
    language: str | None = None
    file_path: str | None = None
    user_edited: bool = False
    source_format: str = "plain"
    raw_source: str | None = None
    agents: dict[str, str] = field(default_factory=dict)
    transliterations: dict[str, str] = field(default_factory=dict)
    rtl: bool = False
    _line_starts: list[int] = field(default_factory=list, repr=False)
    _word_starts: list[list[int]] = field(default_factory=list, repr=False)

    def __post_init__(self):
        self._reindex()

    def _reindex(self):
        self._line_starts = [line.start_time_ms + self.offset_ms for line in self.lines]
        self._word_starts = [
            [word.start_time_ms + self.offset_ms for word in line.words] for line in self.lines
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
        self._reindex()

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

    def line_view(self) -> "LyricsTimeline":
        """Return a line-only view over this document without changing its source."""
        return LyricsTimeline(
            lines=[
                LyricsLine(
                    line.start_time_ms,
                    line.end_time_ms,
                    line.text,
                    speaker_agent=line.speaker_agent,
                    speaker_name=line.speaker_name,
                    line_id=line.line_id,
                    background_vocals=list(line.background_vocals),
                    transliteration=line.transliteration,
                )
                for line in self.lines
            ],
            metadata=dict(self.metadata),
            offset_ms=self.offset_ms,
            synchronized=self.synchronized,
            provider=self.provider,
            language=self.language,
            file_path=self.file_path,
            user_edited=self.user_edited,
            source_format=self.source_format,
            raw_source=self.raw_source,
            agents=dict(self.agents),
            transliterations=dict(self.transliterations),
            rtl=self.rtl,
        )


def _timestamp_ms(match: re.Match) -> int:
    return (
        int(match.group("minutes")) * 60_000
        + int(match.group("seconds")) * 1_000
        + _fraction_ms(match.group("fraction"))
    )


def _parse_word_segments(
    text: str, *, line_start_ms: int | None = None
) -> tuple[str, list[tuple[int, str]]]:
    matches = list(WORD_TIMESTAMP.finditer(text))
    if not matches:
        return text.strip(), []
    words: list[tuple[int, str]] = []
    # Older enhanced-LRC files commonly use the line timestamp for the first
    # word, then put ``<timestamp>`` markers only before subsequent words:
    # ``[00:22.55]I'm <00:23.30>all ...``.  Keep that first segment in the
    # word timeline instead of silently leaving the start of the line
    # unsynchronized.
    prefix = text[: matches[0].start()].strip()
    if prefix and line_start_ms is not None:
        words.append((line_start_ms, prefix))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        word = text[match.end() : end].strip()
        if word:
            words.append((_timestamp_ms(match), word))
    plain = WORD_TIMESTAMP.sub("", text).strip()
    if not plain:
        plain = " ".join(word for _, word in words)
    return plain, words


def parse_lrc(
    content: str, *, provider: str | None = None, file_path: str | None = None
) -> LyricsTimeline:
    metadata: dict[str, Any] = {}
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
        plain, words = _parse_word_segments(text, line_start_ms=_timestamp_ms(timestamps[0]))
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
                end_time_ms=(
                    word_specs[word_index + 1][0] if word_index + 1 < len(word_specs) else end
                ),
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
        source_format="lrc",
        raw_source=content,
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":", 1)[-1]


def _attr(element: ET.Element, name: str, namespace: str | None = None) -> str | None:
    if namespace:
        value = element.get(f"{{{namespace}}}{name}")
        if value is not None:
            return value
    value = element.get(name)
    if value is not None:
        return value
    for key, candidate in element.attrib.items():
        if _local_name(key) == name:
            return candidate
    return None


def _text_content(element: ET.Element) -> str:
    return "".join(element.itertext())


def _ttml_time_ms(value: str | None) -> int:
    if value is None or not str(value).strip():
        raise LyricsParseError("TTML element is missing a time")
    value = str(value).strip()
    try:
        parts = value.split(":")
        if len(parts) == 3:
            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            seconds = int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 1:
            seconds = float(parts[0])
        else:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise LyricsParseError(f"Invalid TTML time: {value!r}") from exc
    if seconds < 0:
        raise LyricsParseError(f"Negative TTML time: {value!r}")
    return round(seconds * 1000)


def _find_elements(root: ET.Element, name: str):
    return (element for element in root.iter() if _local_name(element.tag) == name)


def _parse_ttml_agents(root: ET.Element) -> dict[str, str]:
    agents: dict[str, str] = {}
    for agent in _find_elements(root, "agent"):
        agent_id = _attr(agent, "id", XML_NS) or _attr(agent, "id")
        if not agent_id:
            continue
        name = next(
            (
                value.strip()
                for item in agent.iter()
                if _local_name(item.tag) == "name"
                for value in [_text_content(item)]
                if value.strip()
            ),
            "",
        )
        agents[agent_id] = name or agent_id
    return agents


def _parse_transliterations(root: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in _find_elements(root, "transliteration"):
        language = _attr(item, "lang", XML_NS) or _attr(item, "lang") or ""
        for text in (child for child in item.iter() if _local_name(child.tag) == "text"):
            line_id = _attr(text, "for") or ""
            if line_id:
                result[f"{language}:{line_id}" if language else line_id] = _text_content(
                    text
                ).strip()
    return result


def parse_ttml(
    content: str, *, provider: str | None = None, file_path: str | None = None
) -> LyricsTimeline:
    if not content or not content.strip():
        raise LyricsParseError("Empty TTML document")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise LyricsParseError("Malformed TTML XML") from exc
    if _local_name(root.tag) != "tt":
        raise LyricsParseError("Lyrics XML is not a TTML document")

    language = _attr(root, "lang", XML_NS) or _attr(root, "lang")
    agents = _parse_ttml_agents(root)
    transliterations = _parse_transliterations(root)
    lines: list[LyricsLine] = []
    for paragraph in _find_elements(root, "p"):
        begin = _attr(paragraph, "begin")
        if begin is None:
            continue
        start = _ttml_time_ms(begin)
        end_value = _attr(paragraph, "end")
        end = _ttml_time_ms(end_value) if end_value is not None else None
        agent = _attr(paragraph, "agent", TTM_NS)
        line_id = _attr(paragraph, "key", ITUNES_NS) or _attr(paragraph, "id", XML_NS)
        words: list[LyricsWord] = []

        def collect(element: ET.Element, background: bool = False):
            for child in list(element):
                is_background = background or _attr(child, "role", TTM_NS) == "x-bg"
                if _local_name(child.tag) == "span":
                    child_begin = _attr(child, "begin")
                    if child_begin is not None:
                        child_end = _attr(child, "end")
                        words.append(
                            LyricsWord(
                                text=_text_content(child),
                                start_time_ms=_ttml_time_ms(child_begin),
                                end_time_ms=(
                                    _ttml_time_ms(child_end) if child_end is not None else end
                                ),
                                background_vocal=is_background,
                            )
                        )
                    else:
                        collect(child, is_background)
                else:
                    collect(child, is_background)

        collect(paragraph)
        text = _text_content(paragraph)
        if not text.strip() and not words:
            continue
        speaker_name = agents.get(agent) if agent else None
        transliteration = None
        for key, value in transliterations.items():
            if key.endswith(f":{line_id}") or key == line_id:
                transliteration = value
                break
        lines.append(
            LyricsLine(
                start,
                end,
                text,
                words,
                speaker_agent=agent,
                speaker_name=speaker_name,
                line_id=line_id,
                background_vocals=[word for word in words if word.background_vocal],
                transliteration=transliteration,
            )
        )
    lines.sort(key=lambda line: line.start_time_ms)
    if not lines:
        raise LyricsParseError("TTML document contains no lyric lines")
    metadata = {
        "xml_lang": language or "",
        "timing": _attr(root, "timing", ITUNES_NS) or "",
        "agents": dict(agents),
        "transliterations": dict(transliterations),
    }
    return LyricsTimeline(
        lines=lines,
        metadata=metadata,
        synchronized=True,
        provider=provider,
        language=language,
        file_path=file_path,
        source_format="ttml",
        raw_source=content,
        agents=agents,
        transliterations=transliterations,
        rtl=bool(language and language.split("-", 1)[0].lower() in RTL_LANGUAGES),
    )


def parse_lyrics(
    content: str, *, file_path: str | None = None, provider: str | None = None
) -> LyricsTimeline:
    """Parse TTML/XML, LRC/enhanced-LRC, or plain text based on content."""
    stripped = content.lstrip()
    suffix = (file_path or "").lower()
    if stripped.startswith("<") or suffix.endswith((".ttml", ".xml")):
        return parse_ttml(content, provider=provider, file_path=file_path)
    timeline = parse_lrc(content, provider=provider, file_path=file_path)
    if timeline.lines:
        return timeline
    return LyricsTimeline(
        metadata={},
        synchronized=False,
        provider=provider,
        file_path=file_path,
        source_format="plain",
        raw_source=content,
        lines=[LyricsLine(0, None, line) for line in content.splitlines()],
    )
