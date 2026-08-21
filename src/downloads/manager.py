# manager.py
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

import logging
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from gi.repository import GLib

from ..logging_utils import configure_logger
from ..platform_compat import IS_WINDOWS, subprocess_window_kwargs
from ..runtime import bundled_tool_path, get_python_interpreter_for_tools, is_frozen
from .spotdl import SpotDLCommandResolver, SpotDLUnavailable

LOGGER = logging.getLogger("groovia.spotdl")
configure_logger(LOGGER, "Groovia spotDL")

EventCallback = Callable[[str, "DownloadJob", dict], None]


@dataclass(slots=True)
class DownloadJob:
    id: str
    job_type: str
    source: str
    destination: Path
    sync_file: Path | None = None
    sync_mode: str = "safe"
    output_format: str = "mp3"
    bitrate: str = "auto"
    playlist_id: int | None = None
    state: str = "queued"
    progress: float = 0.0
    track_progress: float = 0.0
    error: str | None = None
    current_track: str = ""
    current_index: int = 0
    completed: int = 0
    total: int = 0
    failed: int = 0
    phase: str = "queued"
    created_at: float = field(default_factory=time.time)
    process: subprocess.Popen | None = field(default=None, repr=False)
    cancel_requested: bool = field(default=False, repr=False)


class ProgressParser:
    """Parse useful progress hints while retaining every raw line for diagnostics."""

    def parse(self, line: str) -> dict:
        import re

        data = {"line": line.rstrip()}
        match = re.search(r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*%", line)
        if match:
            data["track_progress"] = min(100.0, float(match.group(1)))
            # Keep the original parser key for callers that consume parser
            # output directly. The manager treats this as per-track progress.
            data["progress"] = data["track_progress"]
        # Do not mistake search-result counters such as "Found 1/1 result"
        # for a playlist total. Only accept counters tied to a download/item
        # operation; those are the counters that describe the real workload.
        match = re.search(
            r"\b(?:track|song|item|file|download(?:ing|ed)?|processing)\b"
            r"[^\d]{0,80}(\d+)\s*(?:/|of)\s*(\d+)(?!\d)",
            line,
            re.I,
        )
        if match:
            data["current_index"] = int(match.group(1))
            data["total"] = int(match.group(2))
        lowered = line.lower()
        phases = (
            ("already exists", "Reusing existing file"),
            ("skipping", "Reusing existing file"),
            ("embedding", "Writing metadata"),
            ("metadata", "Writing metadata"),
            ("tagging", "Writing metadata"),
            ("converting", "Converting audio"),
            ("processing", "Processing audio"),
            ("downloading", "Downloading audio"),
            ("searching", "Searching for a match"),
            ("matching", "Matching audio"),
            ("found", "Matching audio"),
            ("failed", "Failed"),
            ("error", "Failed"),
        )
        for marker, phase in phases:
            if marker in lowered:
                data["phase"] = phase
                data["current"] = line.strip()
                break
        if re.search(r"\b(downloaded|completed|finished|saved|converted)\b", lowered):
            data["track_done"] = True
            data.setdefault("phase", "Processing audio")
            data.setdefault("current", line.strip())
        if data.get("current_index") and not data.get("track_done"):
            # spotDL usually reports the one-based track currently being
            # handled (for example, "Downloading 3/12").
            data["completed"] = data["current_index"]
        elif data.get("current_index"):
            data["completed"] = data["current_index"]
        if "failed" in data.get("phase", "").lower():
            data["failed"] = 1
        return data


class DownloadManager:
    """Run one controlled spotDL subprocess at a time, independently of playback."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        callback: EventCallback | None = None,
        database=None,
    ):
        self.resolver = SpotDLCommandResolver(data_dir)
        self.callback = callback
        self.database = database
        self._pending: deque[DownloadJob] = deque()
        self._jobs: dict[str, DownloadJob] = {}
        self._lock = threading.RLock()
        self._active: DownloadJob | None = None
        self._dependency_process: subprocess.Popen | None = None
        self._dependency_cancel_requested = False
        if self.database:
            for previous in self.database.download_jobs():
                if previous["state"] in {"queued", "running"}:
                    self.database.save_download_job(
                        previous["id"],
                        previous["job_type"],
                        previous["source"],
                        "interrupted",
                        previous["progress"],
                        previous["destination"],
                        "Groovia closed while this job was running",
                    )

    @property
    def active(self) -> DownloadJob | None:
        return self._active

    def jobs(self) -> list[DownloadJob]:
        return list(self._jobs.values())

    def submit(
        self,
        job_type: str,
        source: str,
        destination: str | Path,
        sync_file: str | Path | None = None,
        sync_mode: str = "safe",
        output_format: str = "mp3",
        bitrate: str = "auto",
        playlist_id: int | None = None,
    ) -> DownloadJob:
        job = DownloadJob(
            id=uuid.uuid4().hex,
            job_type=job_type,
            source=source,
            destination=Path(destination).expanduser().resolve(),
            sync_file=Path(sync_file).expanduser().resolve() if sync_file else None,
            sync_mode=sync_mode,
            output_format=output_format,
            bitrate=bitrate,
            playlist_id=playlist_id,
        )
        if job_type == "track":
            # A single-track job has a known total even when spotDL does not
            # print a 1/1 counter.
            job.total = 1
        job.destination.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._jobs[job.id] = job
            self._pending.append(job)
        self._emit("queued", job)
        self._start_next()
        return job

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.state in {"finished", "failed", "cancelled"}:
            return False
        job.cancel_requested = True
        if job.process:
            try:
                job.process.terminate()
            except OSError:
                pass
        else:
            try:
                self._pending.remove(job)
            except ValueError:
                pass
            job.state = "cancelled"
            self._emit("cancelled", job)
            self._start_next()
        return True

    def retry(self, job_id: str) -> DownloadJob | None:
        old = self._jobs.get(job_id)
        if not old or old.state not in {"failed", "cancelled"}:
            return None
        return self.submit(
            old.job_type,
            old.source,
            old.destination,
            old.sync_file,
            old.sync_mode,
            old.output_format,
            old.bitrate,
            old.playlist_id,
        )

    def _start_next(self):
        with self._lock:
            if self._active or not self._pending:
                return
            self._active = self._pending.popleft()
            job = self._active
            job.state = "running"
        self._emit("started", job)
        threading.Thread(
            target=self._run,
            args=(job,),
            daemon=True,
            name=f"groovia-spotdl-{job.id[:8]}",
        ).start()

    def _command(self, job: DownloadJob) -> list[str]:
        command = list(self.resolver.resolve())
        template = str(job.destination / "{artists} - {title}.{output-ext}")
        if job.job_type == "sync":
            args = [*command, "sync", job.source]
            if job.source.lower().endswith(".spotdl"):
                if job.sync_mode == "safe":
                    args.append("--sync-without-deleting")
            else:
                if job.sync_file:
                    args.extend(["--save-file", str(job.sync_file)])
                if job.sync_mode == "safe":
                    args.append("--sync-without-deleting")
        else:
            args = [*command, "download", job.source]
            if job.sync_file:
                args.extend(["--save-file", str(job.sync_file)])
        args.extend(
            [
                "--output",
                template,
                "--format",
                job.output_format,
                "--overwrite",
                "skip",
                "--restrict",
                "strict",
                "--print-errors",
                "--log-level",
                "INFO",
            ]
        )
        if job.bitrate != "auto":
            args.extend(["--bitrate", job.bitrate])
        supported = self.resolver.supported_options()
        if "--ffmpeg" in supported:
            ffmpeg = bundled_tool_path("ffmpeg")
            if ffmpeg:
                args.extend(["--ffmpeg", str(ffmpeg)])
        return args

    @staticmethod
    def _files(destination: Path) -> set[str]:
        return {
            str(path.resolve())
            for path in destination.rglob("*")
            if path.is_file()
            and path.suffix.lower()
            in {
                ".mp3",
                ".flac",
                ".ogg",
                ".oga",
                ".opus",
                ".wav",
                ".aac",
                ".m4a",
                ".mp4",
                ".lrc",
                ".txt",
            }
        }

    def _run(self, job: DownloadJob):
        before = self._files(job.destination)
        try:
            command = self._command(job)
            LOGGER.info("starting %s: %s", job.job_type, " ".join(command))
            self._emit("command", job, {"command": command})
            job.process = subprocess.Popen(
                command,
                cwd=str(job.destination),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env={**self.resolver.process_environment(), "PYTHONUNBUFFERED": "1"},
                **subprocess_window_kwargs(),
            )
            parser = ProgressParser()
            assert job.process.stdout is not None
            for line in job.process.stdout:
                LOGGER.info("%s", line.rstrip())
                data = parser.parse(line)
                previous_index = job.current_index
                if (
                    data.get("current_index")
                    and data["current_index"] != previous_index
                    and not data.get("track_done")
                ):
                    job.track_progress = 0.0
                if "track_progress" in data:
                    job.track_progress = data["track_progress"]
                if "current" in data:
                    job.current_track = data["current"]
                if "total" in data:
                    job.total = max(job.total, data["total"])
                if "current_index" in data:
                    job.current_index = data["current_index"]
                if "completed" in data:
                    completed = data["completed"]
                    if data.get("current_index") and not data.get("track_done"):
                        completed -= 1
                    job.completed = max(job.completed, completed)
                if "phase" in data:
                    job.phase = data["phase"]
                if "failed" in data:
                    job.failed += data["failed"]
                if job.total:
                    completed = job.completed
                    if data.get("track_done") and job.current_index:
                        completed = max(completed, job.current_index)
                    elif job.current_index:
                        completed = max(completed, job.current_index - 1)
                    job.completed = min(job.total, completed)
                    current = 0.0 if data.get("track_done") else job.track_progress / 100
                    if job.current_index or job.total == 1:
                        job.progress = min(
                            100.0,
                            ((job.completed + current) / job.total) * 100,
                        )
                data.update(
                    {
                        "progress": job.progress,
                        "overall_progress": job.progress if job.total else None,
                        "track_progress": job.track_progress,
                        "completed": job.completed,
                        "total": job.total,
                        "current_index": job.current_index,
                        "failed": job.failed,
                    }
                )
                self._emit("output", job, data)
            returncode = job.process.wait()
            LOGGER.info("spotDL process exited with status %s", returncode)
            if job.cancel_requested:
                job.state = "cancelled"
                self._emit(
                    "cancelled",
                    job,
                    {
                        "returncode": returncode,
                        "files": self._files(job.destination) - before,
                    },
                )
            elif returncode == 0:
                job.state = "finished"
                job.progress = 100.0
                self._emit(
                    "finished",
                    job,
                    {
                        "returncode": returncode,
                        "files": self._files(job.destination) - before,
                        "sync_file": (
                            str(job.sync_file) if job.sync_file and job.sync_file.exists() else None
                        ),
                    },
                )
            else:
                job.state = "failed"
                job.error = f"spotDL exited with status {returncode}"
                self._emit(
                    "failed",
                    job,
                    {
                        "returncode": returncode,
                        "files": self._files(job.destination) - before,
                    },
                )
        except (OSError, SpotDLUnavailable, subprocess.SubprocessError) as error:
            LOGGER.error("spotDL failed: %s", error)
            job.state = "failed"
            job.error = str(error)
            self._emit(
                "failed",
                job,
                {"error": str(error), "files": self._files(job.destination) - before},
            )
        finally:
            job.process = None
            with self._lock:
                self._active = None
            self._start_next()

    def _emit(self, event: str, job: DownloadJob, data: dict | None = None):
        if (
            job
            and self.database
            and event in {"queued", "started", "finished", "failed", "cancelled"}
        ):
            state = {
                "queued": "queued",
                "started": "running",
                "finished": "finished",
                "failed": "failed",
                "cancelled": "cancelled",
            }.get(event, job.state)
            self.database.save_download_job(
                job.id,
                job.job_type,
                job.source,
                state,
                job.progress,
                str(job.destination),
                job.error,
                (
                    time.strftime("%Y-%m-%dT%H:%M:%S%z")
                    if state in {"finished", "failed", "cancelled"}
                    else None
                ),
            )
        if not self.callback:
            return
        payload = data or {}
        # Every callback enters GTK's main loop, including progress and logs.
        GLib.idle_add(self.callback, event, job, payload)

    def install_spotdl(self, callback: EventCallback | None = None):
        """Install spotDL on Linux; Windows tools are staged by the build."""
        self.install_dependencies(callback=callback, install_spotdl=True)

    def install_dependencies(
        self,
        install_ffmpeg: bool = False,
        install_deno: bool = False,
        callback: EventCallback | None = None,
        install_spotdl: bool = False,
    ):
        """Install only explicitly selected components in the managed environment."""
        callback = callback or self.callback

        def emit(event, payload):
            if callback:
                GLib.idle_add(callback, event, None, payload)

        def run_command(command, label):
            emit("dependency-command", {"command": command, "label": label})
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=self.resolver.process_environment(),
                **subprocess_window_kwargs(),
            )
            self._dependency_process = process
            assert process.stdout is not None
            for line in process.stdout:
                emit("dependency-output", {"line": line.rstrip(), "label": label})
                if self._dependency_cancel_requested:
                    process.terminate()
                    break
            returncode = process.wait()
            self._dependency_process = None
            if self._dependency_cancel_requested:
                raise InterruptedError("Dependency installation cancelled")
            if returncode != 0:
                raise subprocess.CalledProcessError(returncode, command)

        def worker():
            self._dependency_cancel_requested = False
            try:
                status = self.resolver.dependency_status()
                LOGGER.info(
                    "Downloader setup: frozen=%s platform=%s executable=%s tool_python=%s environment=%s",
                    is_frozen(),
                    sys.platform,
                    sys.executable,
                    self._tool_python_for_log(),
                    self.resolver.venv_dir,
                )
                emit("dependency-started", {"status": status})
                if IS_WINDOWS:
                    missing = [
                        name
                        for name, present in (
                            ("spotDL", status.spotdl),
                            ("FFmpeg", status.ffmpeg),
                            ("Deno", status.deno),
                        )
                        if not present
                    ]
                    if missing:
                        raise SpotDLUnavailable(
                            "Bundled Windows downloader tools are missing: "
                            + ", ".join(missing)
                            + ". Reinstall Groovia or rebuild the package."
                        )
                    emit(
                        "dependency-output",
                        {
                            "line": (
                                "Bundled Windows downloader tools are installed. "
                                "They are managed by the Groovia installer."
                            )
                        },
                    )
                    emit(
                        "dependency-installed",
                        {
                            "ffmpeg": True,
                            "deno": True,
                            "spotdl": True,
                            "bundled": True,
                        },
                    )
                    return
                if install_spotdl and not status.spotdl:
                    venv = self.resolver.venv_dir
                    venv.parent.mkdir(parents=True, exist_ok=True)
                    run_command(
                        self.resolver.installation_command(),
                        "Creating private Python environment",
                    )
                    python = self.resolver._venv_python()
                    run_command(
                        [str(python), "-m", "pip", "install", "--upgrade", "spotdl"],
                        "Installing spotDL",
                    )
                    self.resolver.invalidate()
                    status = self.resolver.dependency_status()
                elif install_spotdl:
                    emit(
                        "dependency-output",
                        {"line": "spotDL is already available; keeping the existing installation."},
                    )
                command = list(self.resolver.resolve())
                if install_ffmpeg and status.ffmpeg:
                    emit(
                        "dependency-output",
                        {"line": "FFmpeg is already available; skipping overwrite."},
                    )
                elif install_ffmpeg:
                    run_command([*command, "--download-ffmpeg"], "Installing FFmpeg")
                if install_deno and status.deno:
                    emit(
                        "dependency-output",
                        {"line": "Deno is already available; skipping overwrite."},
                    )
                elif install_deno:
                    run_command([*command, "--download-deno"], "Installing Deno")
                emit(
                    "dependency-installed",
                    {"ffmpeg": install_ffmpeg, "deno": install_deno, "spotdl": True},
                )
            except InterruptedError:
                emit("dependency-cancelled", {})
            except Exception as error:
                emit("dependency-failed", {"error": str(error)})
            finally:
                self._dependency_process = None
                self._dependency_cancel_requested = False

        threading.Thread(target=worker, daemon=True, name="groovia-dependency-install").start()

    @staticmethod
    def _tool_python_for_log() -> str:
        try:
            return str(get_python_interpreter_for_tools())
        except Exception as error:
            return f"unavailable: {error}"

    def cancel_dependency_installation(self) -> bool:
        if self._dependency_process is None:
            return False
        self._dependency_cancel_requested = True
        try:
            self._dependency_process.terminate()
        except OSError:
            pass
        return True

    def verify_tools(self, callback: EventCallback | None = None):
        """Probe downloader executables without changing the installation."""
        callback = callback or self.callback

        def worker():
            results = self.resolver.verify_tools()
            if callback:
                GLib.idle_add(callback, "dependency-verified", None, {"tools": results})

        threading.Thread(target=worker, daemon=True, name="groovia-dependency-verify").start()

    def remove_managed_dependencies(self) -> list[str]:
        """Remove only Groovia's private venv and spotDL tool home."""
        removed = []
        for path in self.resolver.managed_dependency_paths():
            if path.exists():
                shutil.rmtree(path)
                removed.append(str(path))
        self.resolver.invalidate()
        return removed
