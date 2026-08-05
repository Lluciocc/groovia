"""Small, persistent and failure-tolerant audio analysis cache.

The analyzer deliberately treats every optional signal as a hint. A missing
ffmpeg/ffprobe or an unreadable file never prevents playback; the planner
falls back to a conservative transition in that case.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TrackAnalysis:
    path: str
    signature: str
    duration: float = 0.0
    bpm: float | None = None
    beat_confidence: float = 0.0
    loudness_lufs: float | None = None
    peak_db: float | None = None
    intro_silence: float = 0.0
    outro_silence: float = 0.0
    energy: float | None = None
    dynamic_range: float | None = None
    key: str | None = None
    vocal_density: float | None = None
    phrase_boundaries: tuple[float, ...] = ()


class AnalysisCache:
    """JSON cache keyed by canonical path and file signature."""

    def __init__(self, data_dir: str | Path | None = None):
        base = Path(data_dir or os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        self.path = base / "groovia" / "autodj" / "analysis.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._items: dict[str, dict] = {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._items = loaded
        except (OSError, ValueError, TypeError):
            self._items = {}

    @staticmethod
    def signature(path: str | Path) -> str:
        resolved = str(Path(path).expanduser().resolve())
        try:
            stat = Path(resolved).stat()
            return f"{resolved}:{stat.st_mtime_ns}:{stat.st_size}"
        except OSError:
            return f"{resolved}:missing"

    def get(self, path: str | Path) -> TrackAnalysis | None:
        signature = self.signature(path)
        with self._lock:
            row = self._items.get(signature)
        if not isinstance(row, dict):
            return None
        try:
            row["phrase_boundaries"] = tuple(row.get("phrase_boundaries") or ())
            return TrackAnalysis(**row)
        except (TypeError, ValueError):
            return None

    def put(self, analysis: TrackAnalysis) -> None:
        with self._lock:
            self._items[analysis.signature] = asdict(analysis)
            self._items[analysis.signature]["phrase_boundaries"] = list(analysis.phrase_boundaries)
            try:
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(self._items, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(self.path)
            except OSError:
                # Analysis remains useful for this session even if the cache is
                # read-only (for example on a restricted Flatpak filesystem).
                pass


class TrackAnalyzer:
    """Analyze only once per file and use conservative fallbacks."""

    _silence_start = re.compile(r"silence_start:\s*([0-9.]+)")
    _silence_end = re.compile(r"silence_end:\s*([0-9.]+)")
    _loudness = re.compile(r"\bI:\s*(-?[0-9.]+)\s*LUFS")
    _peak = re.compile(r"\bPeak:\s*(-?[0-9.]+)\s*dBFS")

    def __init__(self, cache: AnalysisCache | None = None):
        self.cache = cache or AnalysisCache()
        self.ffmpeg = shutil.which("ffmpeg")
        self.ffprobe = shutil.which("ffprobe")

    def analyze(self, track) -> TrackAnalysis:
        path = str(Path(track.path).expanduser().resolve())
        cached = self.cache.get(path)
        if cached:
            print(
                f"[Groovia Auto DJ] analysis ready (cache) track={getattr(track, 'title', Path(path).name)!r} "
                f"bpm={cached.bpm or 'unknown'} lufs={cached.loudness_lufs or 'unknown'}",
                flush=True,
            )
            return cached

        duration = float(getattr(track, "duration", 0.0) or 0.0)
        tags = self._probe_tags(path)
        if tags.get("duration"):
            duration = max(duration, float(tags["duration"]))
        intro, outro = self._silence(path, duration)
        loudness, peak = self._loudness_values(path)
        analysis = TrackAnalysis(
            path=path,
            signature=self.cache.signature(path),
            duration=duration,
            bpm=self._number(tags.get("bpm")),
            beat_confidence=1.0 if tags.get("bpm") else 0.0,
            loudness_lufs=loudness,
            peak_db=peak,
            intro_silence=intro,
            outro_silence=outro,
            energy=None,
            dynamic_range=None,
            key=tags.get("key"),
            vocal_density=None,
            phrase_boundaries=(),
        )
        self.cache.put(analysis)
        print(
            f"[Groovia Auto DJ] analysis ready track={getattr(track, 'title', Path(path).name)!r} "
            f"bpm={analysis.bpm or 'unknown'} lufs={analysis.loudness_lufs or 'unknown'} "
            f"intro_silence={analysis.intro_silence:.2f}s outro_silence={analysis.outro_silence:.2f}s",
            flush=True,
        )
        return analysis

    def _probe_tags(self, path: str) -> dict[str, str]:
        if not self.ffprobe or not Path(path).is_file():
            return {}
        command = [
            self.ffprobe, "-v", "error", "-show_entries",
            "format=duration:format_tags=BPM,TBPM,KEY", "-of", "json", path,
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=12, check=False)
            payload = json.loads(result.stdout or "{}")
            format_data = payload.get("format") or {}
            tags = {str(k).lower(): str(v) for k, v in (format_data.get("tags") or {}).items()}
            if format_data.get("duration"):
                tags["duration"] = str(format_data["duration"])
            tags["bpm"] = tags.get("bpm") or tags.get("tbpm") or ""
            tags["key"] = tags.get("key") or ""
            return tags
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            return {}

    def _silence(self, path: str, duration: float) -> tuple[float, float]:
        if not self.ffmpeg or not Path(path).is_file():
            return 0.0, 0.0
        command = [
            self.ffmpeg, "-hide_banner", "-nostats", "-i", path,
            "-af", "silencedetect=noise=-45dB:d=0.35", "-f", "null", "-",
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
            output = f"{result.stdout}\n{result.stderr}"
            starts = [float(value) for value in self._silence_start.findall(output)]
            ends = [float(value) for value in self._silence_end.findall(output)]
            intro = starts[0] if starts and starts[0] <= 2.0 else 0.0
            last_end = ends[-1] if ends else 0.0
            outro = max(0.0, duration - last_end) if last_end and duration else 0.0
            return min(intro, 2.0), min(outro, 2.0)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return 0.0, 0.0

    def _loudness_values(self, path: str) -> tuple[float | None, float | None]:
        if not self.ffmpeg or not Path(path).is_file():
            return None, None
        command = [
            self.ffmpeg, "-hide_banner", "-nostats", "-i", path,
            "-af", "ebur128=framelog=verbose", "-f", "null", "-",
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
            output = f"{result.stdout}\n{result.stderr}"
            loudness = self._loudness.findall(output)
            peaks = self._peak.findall(output)
            return (float(loudness[-1]) if loudness else None,
                    float(peaks[-1]) if peaks else None)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None, None

    @staticmethod
    def _number(value):
        try:
            parsed = float(value)
            return parsed if 20 <= parsed <= 300 else None
        except (TypeError, ValueError):
            return None
