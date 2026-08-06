"""Small, safe integration layer around the official spotDL CLI."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from ..platform_compat import (
    IS_WINDOWS,
    get_data_dir,
    get_managed_executable_name,
    subprocess_window_kwargs,
)
from ..runtime import bundled_tool_path, is_frozen

SPOTIFY_ID = re.compile(r"^[A-Za-z0-9]{22}$")
SPOTIFY_URL = re.compile(
    r"^https?://open\.spotify\.com/(?:intl-[^/]+/)?(?P<kind>track|playlist|album)/(?P<id>[A-Za-z0-9]{22})(?:[/?#].*)?$",
    re.IGNORECASE,
)
SYNC_SUFFIX = ".spotdl"
AUDIO_SUFFIXES = {
    ".mp3",
    ".flac",
    ".ogg",
    ".oga",
    ".opus",
    ".wav",
    ".aac",
    ".m4a",
    ".mp4",
}


@dataclass(frozen=True, slots=True)
class SourceInfo:
    kind: str
    value: str
    spotify_id: str | None = None


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    spotdl: bool
    ffmpeg: bool
    deno: bool
    python: bool
    pip: bool
    command: tuple[str, ...] | None = None
    bundled: bool = False


class SpotDLUnavailable(RuntimeError):
    pass


def classify_input(value: str) -> SourceInfo:
    value = value.strip()
    path = Path(value).expanduser()
    if path.suffix.lower() == SYNC_SUFFIX and path.is_file():
        return SourceInfo("sync", str(path.resolve()))
    match = SPOTIFY_URL.match(value)
    if match:
        return SourceInfo(match.group("kind").lower(), value, match.group("id"))
    return SourceInfo("invalid", value)


def spotify_id_from_url(value: str) -> str | None:
    match = SPOTIFY_URL.match(value.strip())
    return match.group("id") if match else None


def sanitize_component(value: str, fallback: str = "playlist") -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip().strip(".")
    return value[:120] or fallback


class SpotDLCommandResolver:
    """Resolve one working spotDL command and cache it for the session."""

    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = (
            Path(data_dir) / "groovia" if data_dir else get_data_dir()
        )
        self._command: tuple[str, ...] | None = None
        self._supported_options: set[str] | None = None

    @property
    def venv_dir(self) -> Path:
        return self.data_dir / "downloader" / "venv"

    @property
    def managed_home(self) -> Path:
        """Private HOME used by spotDL for its FFmpeg/Deno/config files."""
        return self.data_dir / "downloader" / "home"

    def process_environment(self) -> dict[str, str]:
        """Keep spotDL's downloaded tools inside Groovia's managed directory."""
        environment = dict(os.environ)
        environment["HOME"] = str(self.managed_home)
        environment["XDG_CONFIG_HOME"] = str(self.managed_home / ".config")
        if IS_WINDOWS:
            environment["USERPROFILE"] = str(self.managed_home)
            environment["APPDATA"] = str(self.managed_home / "AppData" / "Roaming")
            environment["LOCALAPPDATA"] = str(self.managed_home / "AppData" / "Local")
            tools_dir = self.tool_dir()
            if tools_dir:
                environment["PATH"] = str(tools_dir) + os.pathsep + environment.get("PATH", "")
                for name, variable in (
                    ("ffmpeg", "FFMPEG_BINARY"),
                    ("ffprobe", "FFPROBE_BINARY"),
                    ("deno", "DENO_BINARY"),
                ):
                    tool = bundled_tool_path(name, tools_dir)
                    if tool:
                        environment[variable] = str(tool)
                environment["DENO_DIR"] = str(self.managed_home / "deno")
        return environment

    def tool_dir(self) -> Path | None:
        for name in ("spotdl", "ffmpeg", "ffprobe", "deno"):
            tool = bundled_tool_path(name)
            if tool:
                return tool.parent
        return None

    def candidates(self) -> list[tuple[str, ...]]:
        candidates: list[tuple[str, ...]] = []
        bundled = bundled_tool_path("spotdl")
        if bundled:
            candidates.append((str(bundled),))
        spotdl = shutil.which(get_managed_executable_name("spotdl"))
        if spotdl and not (IS_WINDOWS and is_frozen()):
            candidates.append((spotdl,))
        if not IS_WINDOWS:
            python = shutil.which("python3") or shutil.which("python") or sys.executable
            candidates.append((python, "-m", "spotdl"))
        venv_spotdl = self.venv_dir / "bin" / get_managed_executable_name("spotdl")
        if not IS_WINDOWS and venv_spotdl.exists():
            candidates.append((str(venv_spotdl),))
        return candidates

    def resolve(self, refresh: bool = False) -> tuple[str, ...]:
        if self._command and not refresh:
            return self._command
        for candidate in self.candidates():
            try:
                result = subprocess.run(
                    [*candidate, "--version"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=5,
                    check=False,
                    env=self.process_environment(),
                    **subprocess_window_kwargs(),
                )
                if result.returncode == 0:
                    self._command = candidate
                    return candidate
            except (OSError, subprocess.SubprocessError):
                continue
        raise SpotDLUnavailable("spotDL is not installed or is not executable")

    def dependency_status(self) -> DependencyStatus:
        command = None
        try:
            command = self.resolve()
        except SpotDLUnavailable:
            pass
        return DependencyStatus(
            spotdl=command is not None,
            ffmpeg=self._binary_available("ffmpeg"),
            deno=self._binary_available("deno"),
            python=False if IS_WINDOWS else (not is_frozen() or self._venv_python().is_file()),
            pip=False if IS_WINDOWS else (shutil.which("pip3") is not None or shutil.which("pip") is not None),
            command=command,
            bundled=IS_WINDOWS and bundled_tool_path("spotdl") is not None,
        )

    def _binary_available(self, name: str) -> bool:
        bundled = bundled_tool_path(name)
        if bundled:
            return True
        if IS_WINDOWS and is_frozen():
            return False
        if shutil.which(get_managed_executable_name(name)):
            return True
        executable = get_managed_executable_name(name)
        managed = (
            self.venv_dir / ("Scripts" if IS_WINDOWS else "bin") / executable,
            self.data_dir / "downloader" / executable,
            self.managed_home / ".config" / "spotdl" / executable,
            self.managed_home / ".spotdl" / executable,
        )
        return any(path.exists() and path.is_file() for path in managed)

    def invalidate(self) -> None:
        self._command = None
        self._supported_options = None

    def supported_options(self) -> set[str]:
        """Read the installed CLI help before using optional flags."""
        if self._supported_options is not None:
            return self._supported_options
        try:
            result = subprocess.run(
                [*self.resolve(), "--help"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=8,
                check=False,
                env=self.process_environment(),
                **subprocess_window_kwargs(),
            )
            self._supported_options = set(re.findall(r"--[a-z0-9-]+", result.stdout))
        except (OSError, subprocess.SubprocessError, SpotDLUnavailable):
            self._supported_options = set()
        return self._supported_options

    def installation_command(self) -> list[str]:
        if IS_WINDOWS:
            raise SpotDLUnavailable(
                "Windows downloader tools are staged at build time; "
                "runtime venv and pip installation are disabled."
            )
        self.venv_dir.parent.mkdir(parents=True, exist_ok=True)
        return [sys.executable, "-m", "venv", str(self.venv_dir)]

    def _venv_python(self) -> Path:
        return self.venv_dir / ("Scripts" if IS_WINDOWS else "bin") / get_managed_executable_name("python")

    def managed_dependency_paths(self) -> tuple[Path, ...]:
        """Return only paths that Groovia itself owns and may safely remove."""
        if IS_WINDOWS:
            return ()
        return (self.venv_dir, self.managed_home)

    def verify_tools(self) -> dict[str, dict[str, str | bool]]:
        """Run version probes and return captured diagnostics for the UI."""
        results: dict[str, dict[str, str | bool]] = {}
        for name in ("spotdl", "ffmpeg", "ffprobe", "deno"):
            path = bundled_tool_path(name)
            if not path and not (IS_WINDOWS and is_frozen()):
                path = Path(shutil.which(get_managed_executable_name(name)) or "")
            if not path or not path.is_file():
                results[name] = {"available": False, "error": "not found"}
                continue
            argument = "--version" if name in {"spotdl", "deno"} else "-version"
            try:
                result = subprocess.run(
                    [str(path), argument],
                    cwd=str(path.parent),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=15,
                    check=False,
                    env=self.process_environment(),
                    **subprocess_window_kwargs(),
                )
                output = (result.stdout or "").strip().splitlines()
                results[name] = {
                    "available": result.returncode == 0,
                    "version": output[0] if output else "",
                    "error": "" if result.returncode == 0 else f"exit {result.returncode}",
                }
            except (OSError, subprocess.SubprocessError) as error:
                results[name] = {"available": False, "error": str(error)}
        return results


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def read_sync_metadata(path: str | Path) -> list[dict]:
    """Read spotDL's JSON sync file without depending on private spotDL classes."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    entries = []
    for item in _walk_dicts(payload):
        spotify_id = (
            item.get("song_id") or item.get("spotify_id") or item.get("track_id")
        )
        if isinstance(spotify_id, str) and spotify_id.startswith("spotify:"):
            spotify_id = spotify_id.rsplit(":", 1)[-1]
        if not isinstance(spotify_id, str) or not SPOTIFY_ID.match(spotify_id):
            url = item.get("song_url") or item.get("spotify_url") or item.get("url")
            spotify_id = spotify_id_from_url(url) if isinstance(url, str) else None
        if not spotify_id:
            continue
        artist = item.get("artist") or item.get("artists") or ""
        if isinstance(artist, list):
            artist = ", ".join(str(value) for value in artist)
        entries.append(
            {
                "spotify_id": spotify_id,
                "isrc": item.get("isrc"),
                "title": item.get("name") or item.get("title") or "",
                "artist": artist,
                "album": item.get("album") or item.get("album_name") or "",
                "list_name": item.get("list_name") or item.get("playlist_name") or "",
                "cover_url": item.get("cover_url")
                or item.get("album_art")
                or item.get("image"),
            }
        )
    unique = {}
    for entry in entries:
        unique.setdefault(entry["spotify_id"], entry)
    return list(unique.values())


def read_sync_source(path: str | Path) -> tuple[str | None, str | None]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None, None
    query = payload.get("query") if isinstance(payload, dict) else None
    if not isinstance(query, list):
        return None, None
    for value in query:
        if isinstance(value, str):
            source_id = spotify_id_from_url(value)
            if source_id:
                return value, source_id
    return None, None
