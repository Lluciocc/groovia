# analysis.py
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

import json
import logging
import math
import re
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ..platform_compat import get_data_dir, get_managed_executable_name, subprocess_window_kwargs

LOGGER = logging.getLogger("groovia.autodj")
from ..logging_utils import configure_logger

configure_logger(LOGGER, "Groovia Auto DJ")
ANALYSIS_SCHEMA_VERSION = 5


@dataclass(frozen=True, slots=True)
class TrackAnalysis:
    path: str
    signature: str
    duration: float = 0.0
    analysis_schema_version: int = ANALYSIS_SCHEMA_VERSION
    bpm: float | None = None
    source_bpm: str = "unknown"
    tempo_candidates: tuple[float, ...] = ()
    tempo_selection_reason: str = "unknown"
    beat_confidence: float = 0.0
    tempo_stability: float = 0.0
    beats: tuple[float, ...] = ()
    downbeats: tuple[float, ...] = ()
    loudness_lufs: float | None = None
    peak_db: float | None = None
    intro_silence: float = 0.0
    outro_silence: float = 0.0
    energy: float | None = None
    energy_curve: tuple[float, ...] = ()
    dynamic_range: float | None = None
    key: str | None = None
    key_confidence: float = 0.0
    vocal_density: float | None = None
    vocal_curve: tuple[float, ...] = ()
    vocal_sections: tuple[tuple[float, float], ...] = ()
    vocal_entry_points: tuple[float, ...] = ()
    vocal_exit_points: tuple[float, ...] = ()
    lyrics_source: str | None = None
    lyrics_sync_quality: str | None = None
    phrase_boundaries: tuple[float, ...] = ()


class AnalysisCache:
    """Versioned JSON cache keyed by canonical path and file signature."""

    def __init__(self, data_dir: str | Path | None = None, max_entries: int = 500):
        self.path = (
            Path(data_dir) / "groovia" / "autodj" / "analysis.json"
            if data_dir
            else get_data_dir() / "autodj" / "analysis.json"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_entries = max(50, int(max_entries))
        self._lock = threading.RLock()
        self._items: dict[str, dict] = {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._items = loaded
        except (OSError, ValueError, TypeError):
            LOGGER.warning("Auto DJ analysis cache unreadable; rebuilding it")

    @staticmethod
    def signature(path: str | Path) -> str:
        resolved = str(Path(path).expanduser().resolve())
        try:
            stat = Path(resolved).stat()
            return f"{resolved}:{stat.st_mtime_ns}:{stat.st_size}"
        except OSError:
            return f"{resolved}:missing"

    @staticmethod
    def _tuple_fields(row: dict) -> dict:
        for name in (
            "beats",
            "downbeats",
            "energy_curve",
            "vocal_curve",
            "phrase_boundaries",
            "vocal_entry_points",
            "vocal_exit_points",
            "tempo_candidates",
        ):
            row[name] = tuple(float(value) for value in (row.get(name) or ()))
        row["vocal_sections"] = tuple(
            (float(pair[0]), float(pair[1]))
            for pair in (row.get("vocal_sections") or ())
            if isinstance(pair, (list, tuple)) and len(pair) == 2
        )
        return row

    def get(self, path: str | Path) -> TrackAnalysis | None:
        signature = self.signature(path)
        with self._lock:
            row = dict(self._items.get(signature) or {})
        if not row or row.get("analysis_schema_version") != ANALYSIS_SCHEMA_VERSION:
            return None
        try:
            return TrackAnalysis(**self._tuple_fields(row))
        except (TypeError, ValueError):
            LOGGER.warning("Ignoring incompatible Auto DJ cache row for %s", path)
            return None

    def put(self, analysis: TrackAnalysis) -> None:
        with self._lock:
            self._items[analysis.signature] = asdict(analysis)
            self._items[analysis.signature]["beats"] = list(analysis.beats)
            self._items[analysis.signature]["downbeats"] = list(analysis.downbeats)
            self._items[analysis.signature]["tempo_candidates"] = list(analysis.tempo_candidates)
            self._items[analysis.signature]["energy_curve"] = list(analysis.energy_curve)
            self._items[analysis.signature]["vocal_curve"] = list(analysis.vocal_curve)
            self._items[analysis.signature]["vocal_sections"] = [
                list(x) for x in analysis.vocal_sections
            ]
            self._items[analysis.signature]["vocal_entry_points"] = list(
                analysis.vocal_entry_points
            )
            self._items[analysis.signature]["vocal_exit_points"] = list(analysis.vocal_exit_points)
            self._items[analysis.signature]["phrase_boundaries"] = list(analysis.phrase_boundaries)
            # A bounded cache avoids retaining analyses for deleted libraries.
            if len(self._items) > self.max_entries:
                for key in list(self._items)[: len(self._items) - self.max_entries]:
                    self._items.pop(key, None)
            try:
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(self._items, ensure_ascii=False), encoding="utf-8")
                temporary.replace(self.path)
            except OSError:
                LOGGER.warning("Could not persist Auto DJ analysis cache", exc_info=True)


class TrackAnalyzer:
    """Analyze a track once, with graceful DSP fallbacks."""

    _silence_start = re.compile(r"silence_start:\s*([0-9.]+)")
    _silence_end = re.compile(r"silence_end:\s*([0-9.]+)")
    _loudness = re.compile(r"\bI:\s*(-?[0-9.]+)\s*LUFS")
    _peak = re.compile(r"\bPeak:\s*(-?[0-9.]+)\s*dBFS")

    def __init__(self, cache: AnalysisCache | None = None, lyrics_provider=None):
        self.cache = cache or AnalysisCache()
        self.lyrics_provider = lyrics_provider
        self.ffmpeg = shutil.which(get_managed_executable_name("ffmpeg"))
        self.ffprobe = shutil.which(get_managed_executable_name("ffprobe"))

    def analyze(self, track) -> TrackAnalysis:
        started = time.perf_counter()
        path = str(Path(track.path).expanduser().resolve())
        cached = self.cache.get(path)
        if cached:
            LOGGER.info(
                "analysis cache_hit track=%r bpm=%s confidence=%.2f beats=%d phrases=%d "
                "tempo_candidates=%s selected_bpm=%s reason=%s",
                getattr(track, "title", Path(path).name),
                cached.bpm or "unknown",
                cached.beat_confidence,
                len(cached.beats),
                len(cached.phrase_boundaries),
                list(cached.tempo_candidates),
                cached.bpm or "unknown",
                cached.tempo_selection_reason,
            )
            return cached

        LOGGER.info("analysis start track=%r", getattr(track, "title", Path(path).name))
        duration = float(getattr(track, "duration", 0.0) or 0.0)
        tags = self._probe_tags(path)
        if tags.get("duration"):
            duration = max(duration, float(tags["duration"]))
        intro, outro = self._silence(path, duration)
        loudness, peak = self._loudness_values(path)
        pcm, sample_rate, decode_seconds = self._decode_pcm(path)
        if duration <= 0 and pcm is not None and sample_rate:
            duration = len(pcm) / sample_rate
        tagged_bpm = self._number(tags.get("bpm"))
        dsp = self._analyze_pcm(pcm, sample_rate, duration, tagged_bpm) if pcm is not None else {}
        detected_bpm = dsp.get("bpm")
        if detected_bpm is not None and dsp.get("beat_confidence", 0.0) >= 0.25:
            bpm, source_bpm = detected_bpm, "audio"
        elif tagged_bpm is not None:
            bpm, source_bpm = tagged_bpm, "metadata"
        else:
            bpm, source_bpm = None, "unknown"
        lyrics = self._lyrics_features(track, duration)
        analysis = TrackAnalysis(
            path=path,
            signature=self.cache.signature(path),
            duration=duration,
            bpm=bpm,
            source_bpm=source_bpm,
            tempo_candidates=tuple(dsp.get("tempo_candidates", ()))
            or ((tagged_bpm,) if tagged_bpm else ()),
            tempo_selection_reason=dsp.get(
                "tempo_selection_reason",
                "metadata" if tagged_bpm and detected_bpm is None else "unknown",
            ),
            beat_confidence=float(dsp.get("beat_confidence", 0.0)),
            tempo_stability=float(dsp.get("tempo_stability", 0.0)),
            beats=tuple(dsp.get("beats", ())),
            downbeats=tuple(dsp.get("downbeats", ())),
            loudness_lufs=loudness,
            peak_db=peak,
            intro_silence=intro,
            outro_silence=outro,
            energy=dsp.get("energy"),
            energy_curve=tuple(dsp.get("energy_curve", ())),
            dynamic_range=dsp.get("dynamic_range"),
            key=dsp.get("key") or tags.get("key") or None,
            key_confidence=float(
                dsp.get("key_confidence", 0.0) or (0.25 if tags.get("key") else 0.0)
            ),
            vocal_density=lyrics.get("vocal_density", dsp.get("vocal_density")),
            vocal_curve=tuple(lyrics.get("vocal_curve", dsp.get("vocal_curve", ()))),
            vocal_sections=tuple(lyrics.get("vocal_sections", dsp.get("vocal_sections", ()))),
            vocal_entry_points=tuple(
                lyrics.get("vocal_entry_points", dsp.get("vocal_entry_points", ()))
            ),
            vocal_exit_points=tuple(
                lyrics.get("vocal_exit_points", dsp.get("vocal_exit_points", ()))
            ),
            lyrics_source=lyrics.get("lyrics_source"),
            lyrics_sync_quality=lyrics.get("lyrics_sync_quality"),
            phrase_boundaries=tuple(dsp.get("phrase_boundaries", ())),
        )
        self.cache.put(analysis)
        LOGGER.info(
            "analysis complete track=%r total=%.2fs decode=%.2fs bpm=%s source_bpm=%s "
            "beat_confidence=%.2f tempo_stability=%.2f beats=%d downbeats=%d phrases=%d "
            "energy=%s vocal_density=%s vocal_entries=%d vocal_exits=%d lyrics=%s/%s "
            "key=%s key_confidence=%.2f tempo_candidates=%s selected_bpm=%s reason=%s",
            getattr(track, "title", Path(path).name),
            time.perf_counter() - started,
            decode_seconds,
            analysis.bpm or "unknown",
            analysis.source_bpm,
            analysis.beat_confidence,
            analysis.tempo_stability,
            len(analysis.beats),
            len(analysis.downbeats),
            len(analysis.phrase_boundaries),
            f"{analysis.energy:.2f}" if analysis.energy is not None else "unknown",
            f"{analysis.vocal_density:.2f}" if analysis.vocal_density is not None else "unknown",
            len(analysis.vocal_entry_points),
            len(analysis.vocal_exit_points),
            analysis.lyrics_source or "none",
            analysis.lyrics_sync_quality or "none",
            analysis.key or "unknown",
            analysis.key_confidence,
            list(analysis.tempo_candidates),
            analysis.bpm or "unknown",
            analysis.tempo_selection_reason,
        )
        return analysis

    def _probe_tags(self, path: str) -> dict[str, str]:
        if not self.ffprobe or not Path(path).is_file():
            return {}
        command = [
            self.ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:format_tags=BPM,TBPM,KEY",
            "-of",
            "json",
            path,
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
                **subprocess_window_kwargs(),
            )
            payload = json.loads(result.stdout or "{}")
            format_data = payload.get("format") or {}
            tags = {str(k).lower(): str(v) for k, v in (format_data.get("tags") or {}).items()}
            if format_data.get("duration"):
                tags["duration"] = str(format_data["duration"])
            tags["bpm"] = tags.get("bpm") or tags.get("tbpm") or ""
            tags["key"] = tags.get("key") or ""
            return tags
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            LOGGER.debug("ffprobe metadata unavailable for %s", path, exc_info=True)
            return {}

    def _decode_pcm(self, path: str) -> tuple[object | None, int, float]:
        if not self.ffmpeg or not Path(path).is_file() or path.startswith(("http://", "https://")):
            return None, 0, 0.0
        started = time.perf_counter()
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "11025",
            "-f",
            "f32le",
            "-",
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, timeout=90, check=False, **subprocess_window_kwargs()
            )
            if result.returncode:
                return None, 0, time.perf_counter() - started
            try:
                import numpy as np
            except ImportError:
                return None, 0, time.perf_counter() - started
            # Keep analysis bounded for unusually long recordings while still
            # covering the useful musical material in a normal song or mix.
            samples = np.frombuffer(result.stdout, dtype=np.float32)
            limit = 25 * 60 * 11025
            if samples.size > limit:
                samples = samples[:limit]
            return samples, 11025, time.perf_counter() - started
        except (OSError, subprocess.TimeoutExpired):
            LOGGER.debug("PCM decode unavailable for %s", path, exc_info=True)
            return None, 0, time.perf_counter() - started

    @staticmethod
    def _analyze_pcm(
        samples, sample_rate: int, duration: float, metadata_bpm: float | None = None
    ) -> dict:
        if samples is None or len(samples) < sample_rate:
            return {}
        try:
            import numpy as np
            from scipy import signal
        except ImportError:
            return {}
        samples = np.asarray(samples, dtype=np.float32)
        samples = samples - float(np.mean(samples))
        frame = 1024
        # 128 samples keeps beat timestamps useful for phrase alignment while
        # remaining inexpensive at the 11.025 kHz analysis rate.
        hop = 128
        if len(samples) < frame:
            return {}
        window = np.hanning(frame).astype(np.float32)
        count = 1 + (len(samples) - frame) // hop
        frames = np.lib.stride_tricks.as_strided(
            samples,
            shape=(count, frame),
            strides=(samples.strides[0] * hop, samples.strides[0]),
            writeable=False,
        )
        spectrum = np.abs(np.fft.rfft(frames * window, axis=1))
        spectrum = np.log1p(spectrum)
        flux = np.maximum(0.0, np.diff(spectrum, axis=0, prepend=spectrum[:1])).sum(axis=1)
        window = min(9, len(flux) if len(flux) % 2 else len(flux) - 1)
        flux = signal.savgol_filter(flux, window, 2) if window >= 5 else flux
        flux = np.maximum(0.0, flux)
        baseline = signal.medfilt(flux, kernel_size=9 if len(flux) >= 9 else 3)
        onset = np.maximum(0.0, flux - baseline)
        peak_distance = max(1, int(sample_rate * 60 / 220 / hop))
        prominence = max(float(np.percentile(onset, 70)) * 0.18, 1e-5)
        peak_indices, props = signal.find_peaks(
            onset, distance=peak_distance, prominence=prominence
        )
        peak_times = peak_indices * hop / sample_rate
        bpm, confidence, stability, tempo_details = TrackAnalyzer._estimate_tempo_details(
            onset, peak_indices, sample_rate, hop, metadata_bpm
        )
        beats = TrackAnalyzer._beat_timeline(
            peak_times, bpm, duration or len(samples) / sample_rate
        )
        downbeats = tuple(beats[index] for index in range(0, len(beats), 4))
        rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
        db = 20 * np.log10(rms + 1e-7)
        energy_curve = TrackAnalyzer._normalize_curve(db, 48)
        energy = float(np.clip((np.percentile(db, 90) + 60) / 60, 0, 1))
        dynamic_range = float(max(0.0, np.percentile(db, 95) - np.percentile(db, 10)))
        band = spectrum[
            :, max(1, int(250 / (sample_rate / frame))) : max(2, int(4000 / (sample_rate / frame)))
        ]
        vocal_curve = TrackAnalyzer._normalize_curve(band.mean(axis=1), 48)
        vocal_density = float(np.mean(np.asarray(vocal_curve) > 0.52)) if vocal_curve else None
        vocal_sections = TrackAnalyzer._curve_sections(
            vocal_curve, duration or len(samples) / sample_rate
        )
        detected_key, key_confidence = TrackAnalyzer._estimate_key(spectrum, sample_rate, frame)
        phrase_boundaries = TrackAnalyzer._phrase_boundaries(
            beats, downbeats, db, onset, hop, duration or len(samples) / sample_rate
        )
        return {
            "bpm": bpm,
            "beat_confidence": confidence,
            "tempo_stability": stability,
            "tempo_candidates": tuple(item["bpm"] for item in tempo_details),
            "tempo_selection_reason": tempo_details[0]["reason"] if tempo_details else "unknown",
            "beats": beats,
            "downbeats": downbeats,
            "energy": energy,
            "energy_curve": energy_curve,
            "dynamic_range": dynamic_range,
            "vocal_density": vocal_density,
            "vocal_curve": vocal_curve,
            "vocal_sections": vocal_sections,
            "vocal_entry_points": tuple(start for start, _end in vocal_sections),
            "vocal_exit_points": tuple(end for _start, end in vocal_sections),
            "key": detected_key,
            "key_confidence": key_confidence,
            "phrase_boundaries": phrase_boundaries,
        }

    @staticmethod
    def _estimate_key(spectrum, sample_rate: int, frame: int):
        """Estimate a broad major/minor key from a chroma histogram."""
        try:
            import numpy as np
        except ImportError:
            return None, 0.0
        frequencies = np.fft.rfftfreq(frame, 1.0 / sample_rate)
        chroma = np.zeros(12, dtype=float)
        for index, frequency in enumerate(frequencies):
            if 80 <= frequency <= 2000:
                pitch_class = int(round(69 + 12 * math.log2(frequency / 440.0))) % 12
                chroma[pitch_class] += float(np.mean(spectrum[:, index]))
        if not np.any(chroma):
            return None, 0.0
        chroma /= np.linalg.norm(chroma) + 1e-9
        major = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
        minor = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
        major /= np.linalg.norm(major)
        minor /= np.linalg.norm(minor)
        names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
        candidates = []
        for root in range(12):
            candidates.append((float(np.dot(chroma, np.roll(major, root))), names[root], "major"))
            candidates.append((float(np.dot(chroma, np.roll(minor, root))), names[root], "minor"))
        candidates.sort(reverse=True)
        best, second = candidates[0], candidates[1]
        return f"{best[1]} {best[2]}", float(np.clip((best[0] - second[0]) * 3.0, 0, 1))

    @staticmethod
    def _estimate_tempo(onset, peaks, sample_rate: int, hop: int):
        """Backward-compatible tempo API returning only the selected values."""
        bpm, confidence, stability, _details = TrackAnalyzer._estimate_tempo_details(
            onset, peaks, sample_rate, hop
        )
        return bpm, confidence, stability

    @staticmethod
    def _estimate_tempo_details(onset, peaks, sample_rate: int, hop: int, metadata_bpm=None):
        try:
            import numpy as np
            from scipy import signal
        except ImportError:
            return None, 0.0, 0.0, ()
        if len(onset) < 8 or float(np.max(onset)) <= 0:
            return None, 0.0, 0.0, ()
        autocorr = signal.fftconvolve(onset, onset[::-1], mode="full")[len(onset) - 1 :]
        lag_min = max(1, int(60 / 190 * sample_rate / hop))
        lag_max = min(len(autocorr) - 1, int(60 / 55 * sample_rate / hop))
        if lag_max <= lag_min:
            return None, 0.0, 0.0, ()
        lags = np.arange(lag_min, lag_max + 1)
        values = autocorr[lags]
        base_tempos = []
        for index in np.argsort(values)[-10:]:
            lag = int(lags[index])
            raw = 60.0 * sample_rate / (lag * hop)
            base_tempos.append(raw)
        if len(peaks) >= 6:
            # Peak-to-peak regression is more accurate than a single FFT lag
            # for tempos whose period falls between analysis frames.
            peak_times = peaks * hop / sample_rate
            slope = float(np.polyfit(np.arange(len(peak_times)), peak_times, 1)[0])
            direct = 60.0 / slope if slope > 0 else 0.0
            base_tempos.append(direct)

        # Keep the explicit half/double-time hypotheses.  They are scored as
        # musical interpretations rather than being clamped to an arbitrary
        # BPM ceiling.
        hypotheses = set()
        for base in base_tempos:
            if not base or not math.isfinite(base):
                continue
            for tempo in (base / 2.0, base, base * 2.0):
                if 55 <= tempo <= 240:
                    hypotheses.add(round(float(tempo), 3))
        if metadata_bpm and 55 <= metadata_bpm <= 240:
            for tempo in (metadata_bpm / 2.0, metadata_bpm, metadata_bpm * 2.0):
                if 55 <= tempo <= 240:
                    hypotheses.add(round(float(tempo), 3))
        if not hypotheses:
            return None, 0.0, 0.0, ()

        peak_times = peaks * hop / sample_rate
        intervals = np.diff(peak_times) if len(peak_times) > 1 else np.array(())
        scored = []
        for tempo in sorted(hypotheses):
            period = 60.0 / tempo
            lag = min(len(autocorr) - 1, max(1, int(round(period * sample_rate / hop))))
            periodicity = float(autocorr[lag] / (float(autocorr[0]) + 1e-9))
            if len(intervals):
                ratios = intervals / period
                allowed = np.asarray((0.5, 1.0, 2.0, 4.0))
                nearest = np.min(
                    np.abs(ratios[:, None] - allowed[None, :]) / allowed[None, :], axis=1
                )
                beat_consistency = float(np.mean(np.clip(1.0 - nearest * 2.0, 0, 1)))
                stability = float(
                    np.clip(
                        1.0 - np.std(intervals / period) / (np.mean(intervals / period) + 1e-9),
                        0,
                        1,
                    )
                )
                event_ratio = float(np.mean(intervals) / period)
                density_distance = min(abs(event_ratio - value) for value in (0.5, 1.0, 2.0, 4.0))
                event_density = max(0.0, 1.0 - density_distance / 1.5)
            else:
                beat_consistency = stability = event_density = 0.0

            # Coverage of a regular beat grid, with tolerance for subdivisions.
            phase_candidates = np.mod(peak_times, period) if len(peak_times) else np.array(())
            phase = float(np.median(phase_candidates)) if len(phase_candidates) else 0.0
            if len(peak_times):
                grid = np.arange(phase, (peak_times[-1] + period), period)
                coverage = float(
                    np.mean([np.min(np.abs(peak_times - point)) <= period * 0.18 for point in grid])
                )
            else:
                coverage = 0.0
            downbeat_consistency = float(np.clip(0.5 * coverage + 0.5 * periodicity, 0, 1))
            typical_range = 1.0 if 70.0 <= tempo <= 155.0 else 0.56
            metadata_score = 0.0
            if metadata_bpm:
                metadata_delta = abs(math.log(max(tempo, 1e-6) / max(metadata_bpm, 1e-6), 2))
                metadata_score = max(0.0, 1.0 - metadata_delta / 1.2)
            score = (
                0.25 * periodicity
                + 0.23 * beat_consistency
                + 0.12 * downbeat_consistency
                + 0.15 * stability
                + 0.10 * event_density
                + 0.10 * typical_range
                + 0.05 * metadata_score
            )
            # High BPM is not invalid, but without trustworthy metadata it is
            # often the upper interpretation of the same musical pulse. Keep
            # it in the hypothesis set and require stronger evidence to win.
            if tempo > 165.0 and not metadata_bpm:
                score *= 0.86
            scored.append(
                {
                    "bpm": round(tempo, 3),
                    "score": float(score),
                    "periodicity": periodicity,
                    "beat_consistency": beat_consistency,
                    "downbeat_consistency": downbeat_consistency,
                    "stability": stability,
                    "event_density": event_density,
                    "metadata": metadata_score,
                }
            )
        scored.sort(key=lambda item: (item["score"], item["stability"], item["bpm"]), reverse=True)
        best = scored[0]
        second = scored[1] if len(scored) > 1 else None
        margin = best["score"] - second["score"] if second else best["score"]
        confidence = float(np.clip(0.45 + best["score"] * 0.45 + margin * 1.2, 0, 1))
        reason = "highest composite tempo hypothesis"
        if best["bpm"] < max(item["bpm"] for item in scored) / 1.7:
            reason = "half-time interpretation scored higher than double-time pulse"
        elif best["bpm"] > min(item["bpm"] for item in scored) * 1.7:
            reason = "double-time interpretation supported by event density"
        return (
            best["bpm"],
            confidence,
            best["stability"],
            tuple(
                {**item, "reason": reason if item is best else "alternative tempo hypothesis"}
                for item in scored
            ),
        )

    @staticmethod
    def _beat_timeline(peak_times, bpm, duration):
        if bpm is None:
            return tuple(float(value) for value in peak_times)
        interval = 60.0 / bpm
        peaks = list(float(value) for value in peak_times)
        if not peaks:
            return ()
        start = peaks[0]
        end = max(start, duration)
        return tuple(
            round(start + index * interval, 4) for index in range(int((end - start) / interval) + 1)
        )

    @staticmethod
    def _normalize_curve(values, points):
        try:
            import numpy as np

            values = np.asarray(values, dtype=float)
            if not len(values):
                return ()
            edges = np.linspace(0, len(values), points + 1, dtype=int)
            result = [
                float(np.mean(values[edges[i] : edges[i + 1]]))
                for i in range(points)
                if edges[i] < edges[i + 1]
            ]
            low, high = min(result), max(result)
            if high - low < 1e-9:
                return tuple(0.5 for _ in result)
            return tuple(round(float((value - low) / (high - low)), 4) for value in result)
        except (ImportError, ValueError):
            return ()

    @staticmethod
    def _curve_sections(curve, duration):
        """Turn the audio vocal-activity approximation into broad sections."""
        if not curve or duration <= 0:
            return ()
        active = [value >= 0.58 for value in curve]
        sections = []
        start = None
        for index, is_active in enumerate(active + [False]):
            if is_active and start is None:
                start = index / len(active) * duration
            elif not is_active and start is not None:
                end = index / len(active) * duration
                if end - start >= 0.45:
                    sections.append((max(0.0, start - 0.18), min(duration, end + 0.18)))
                start = None
        return tuple(sections)

    @staticmethod
    def _phrase_boundaries(beats, downbeats, energy_db, onset, hop, duration):
        if not beats:
            return ()
        try:
            import numpy as np

            beat_interval = float(np.median(np.diff(np.asarray(beats, dtype=float))))
        except (ImportError, TypeError, ValueError):
            beat_interval = duration / max(len(beats), 1)
        if not math.isfinite(beat_interval) or beat_interval <= 0:
            beat_interval = 0.5

        # A phrase boundary is a semantic anchor, not every downbeat.  Start
        # with eight-bar groups and let strong novelty peaks replace a group
        # anchor.  The spacing/NMS limit keeps long recordings from producing
        # hundreds of interchangeable candidates.
        minimum_spacing = max(12.0, 16.0 * beat_interval)
        candidates = {float(value): 0.28 for value in downbeats[::8] if 0 < value < duration}
        try:
            import numpy as np
            from scipy import signal

            novelty = np.abs(np.diff(energy_db, prepend=energy_db[:1]))
            if len(novelty):
                threshold = float(np.percentile(novelty, 78))
                distance = max(1, int(minimum_spacing * 11025 / hop))
                peaks, properties = signal.find_peaks(
                    novelty, distance=distance, prominence=max(threshold * 0.18, 1e-6)
                )
                prominence = properties.get("prominences", np.zeros(len(peaks)))
                if len(prominence):
                    scale = max(float(np.percentile(prominence, 90)), 1e-6)
                    for index, strength in zip(peaks, prominence):
                        timestamp = index * hop / 11025
                        if timestamp <= 1.0 or index >= len(onset):
                            continue
                        nearest = min(downbeats or beats, key=lambda value: abs(value - timestamp))
                        if abs(nearest - timestamp) <= max(0.8, 1.25 * beat_interval):
                            candidates[float(nearest)] = max(
                                candidates.get(float(nearest), 0.0),
                                0.55 + 0.45 * min(1.0, float(strength) / scale),
                            )
        except (ImportError, ValueError, TypeError):
            pass

        selected = TrackAnalyzer._non_max_suppress(
            candidates.items(), minimum_spacing=minimum_spacing, maximum=96
        )
        return tuple(round(value, 4) for value in selected if 0 < value < duration)

    @staticmethod
    def _non_max_suppress(points, minimum_spacing: float, maximum: int):
        """Keep the strongest structural anchors while enforcing spacing."""
        selected = []
        for value, score in sorted(points, key=lambda item: (-item[1], item[0])):
            if all(abs(value - other) >= minimum_spacing for other in selected):
                selected.append(float(value))
            if len(selected) >= maximum:
                break
        return tuple(sorted(selected))

    def _lyrics_features(self, track, duration: float) -> dict:
        if not self.lyrics_provider:
            return {}
        try:
            result = self.lyrics_provider(track)
            timeline = result[0] if isinstance(result, tuple) else result
            if not timeline or not getattr(timeline, "synchronized", False):
                return {"lyrics_source": "plain" if timeline else None}
            sections = []
            entries = []
            exits = []
            for line in getattr(timeline, "lines", ()):
                offset = getattr(timeline, "offset_ms", 0) / 1000
                line_start = line.start_time_ms / 1000 + offset
                words = getattr(line, "words", ())
                vocal_entry = words[0].start_time_ms / 1000 + offset if words else line_start
                entries.append(max(0.0, vocal_entry))
                start = max(0.0, vocal_entry - (0.08 if words else 0.18))
                next_start = (line.end_time_ms or 0) / 1000 + offset
                if not next_start:
                    index = timeline.lines.index(line)
                    next_start = (
                        timeline.lines[index + 1].start_time_ms / 1000 + offset
                        if index + 1 < len(timeline.lines)
                        else start + 2.0
                    )
                exit_time = min(duration or next_start, next_start + 0.18)
                exits.append(exit_time)
                sections.append((start, exit_time))
            if not sections:
                return {}
            points = 48
            curve = (
                tuple(
                    1.0
                    if any(start <= index / points * duration <= end for start, end in sections)
                    else 0.0
                    for index in range(points)
                )
                if duration
                else ()
            )
            occupied = sum(max(0.0, end - start) for start, end in sections)
            return {
                "vocal_density": min(1.0, occupied / max(duration, 1.0)),
                "vocal_curve": curve,
                "vocal_sections": tuple(sections),
                "vocal_entry_points": tuple(entries),
                "vocal_exit_points": tuple(exits),
                "lyrics_source": getattr(timeline, "provider", None) or "local",
                "lyrics_sync_quality": "word"
                if getattr(timeline, "word_synchronized", False)
                else "line",
            }
        except Exception:
            LOGGER.debug(
                "local lyrics unavailable for %s", getattr(track, "path", track), exc_info=True
            )
            return {}

    def _silence(self, path: str, duration: float) -> tuple[float, float]:
        if not self.ffmpeg or not Path(path).is_file():
            return 0.0, 0.0
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            path,
            "-af",
            "silencedetect=noise=-45dB:d=0.35",
            "-f",
            "null",
            "-",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
                **subprocess_window_kwargs(),
            )
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
            self.ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            path,
            "-af",
            "ebur128=framelog=verbose",
            "-f",
            "null",
            "-",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                **subprocess_window_kwargs(),
            )
            output = f"{result.stdout}\n{result.stderr}"
            loudness, peaks = self._loudness.findall(output), self._peak.findall(output)
            return (float(loudness[-1]) if loudness else None, float(peaks[-1]) if peaks else None)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None, None

    @staticmethod
    def _number(value):
        try:
            parsed = float(str(value).replace(",", "."))
            return parsed if 20 <= parsed <= 300 else None
        except (TypeError, ValueError):
            return None
