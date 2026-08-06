"""Single-worker spotDL job manager with GTK-safe progress events."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from gi.repository import GLib

from ..platform_compat import subprocess_window_kwargs
from .spotdl import SpotDLCommandResolver, SpotDLUnavailable

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
    error: str | None = None
    current_track: str = ""
    completed: int = 0
    total: int = 0
    failed: int = 0
    phase: str = "queued"
    lyrics_mode: str = "none"
    lyrics_fallback: bool = True
    generate_lrc: bool = False
    lyrics_providers: tuple[str, ...] = ()
    sync_remove_lrc: bool = False
    created_at: float = field(default_factory=time.time)
    process: subprocess.Popen | None = field(default=None, repr=False)
    cancel_requested: bool = field(default=False, repr=False)


class ProgressParser:
    """Parse useful progress hints while retaining every raw line for diagnostics."""

    def parse(self, line: str) -> dict:
        import re

        data = {"line": line.rstrip()}
        match = re.search(r"(\d{1,3})%", line)
        if match:
            data["progress"] = min(100.0, float(match.group(1)))
        match = re.search(
            r"(?:track|song|item|file)?\s*(\d+)\s*(?:/|of)\s*(\d+)",
            line,
            re.I,
        )
        if match:
            data["completed"] = int(match.group(1))
            data["total"] = int(match.group(2))
        lowered = line.lower()
        phases = (
            ("downloading", "Downloading"),
            ("searching", "Searching"),
            ("processing", "Processing"),
            ("embedding", "Embedding metadata"),
            ("found", "Matching audio"),
            ("skipping", "Reusing existing file"),
            ("already exists", "Reusing existing file"),
            ("failed", "Failed"),
            ("error", "Failed"),
        )
        for marker, phase in phases:
            if marker in lowered:
                data["phase"] = phase
                data["current"] = line.strip()
                if phase == "Failed":
                    data["failed"] = 1
                break
        return data


LYRICS_PROVIDERS = {"synced", "genius", "azlyrics"}


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
                        lyrics_mode=previous.get("lyrics_mode", "none"),
                        lyrics_fallback=bool(previous.get("lyrics_fallback", 1)),
                        generate_lrc=bool(previous.get("generate_lrc", 0)),
                        lyrics_providers=previous.get("lyrics_providers"),
                        sync_remove_lrc=bool(previous.get("sync_remove_lrc", 0)),
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
        lyrics_mode: str = "none",
        lyrics_fallback: bool = True,
        generate_lrc: bool = False,
        lyrics_providers: tuple[str, ...] = (),
        sync_remove_lrc: bool = False,
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
            lyrics_mode=lyrics_mode,
            lyrics_fallback=lyrics_fallback,
            generate_lrc=generate_lrc,
            lyrics_providers=tuple(lyrics_providers),
            sync_remove_lrc=sync_remove_lrc,
        )
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
            old.lyrics_mode,
            old.lyrics_fallback,
            old.generate_lrc,
            old.lyrics_providers,
            old.sync_remove_lrc,
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
        if job.lyrics_mode != "none" and "--lyrics" in supported:
            selected = tuple(
                provider.lower()
                for provider in (
                    job.lyrics_providers
                    or ("synced", "genius", "musixmatch", "azlyrics")
                )
            )
            # Musixmatch is handled by Groovia's custom richsync client. Never
            # pass it to spotDL: doing so would select the old API path and
            # reintroduce the HTTP 401 failures this backend avoids.
            providers = [
                provider
                for provider in selected
                if provider in LYRICS_PROVIDERS and provider != "musixmatch"
            ]
            if not providers and job.lyrics_fallback:
                providers = ["synced", "genius", "azlyrics"]
            if job.lyrics_mode != "synced":
                providers = [provider for provider in providers if provider != "synced"]
                if not providers and job.lyrics_fallback:
                    providers = ["genius", "azlyrics"]
            if job.lyrics_fallback:
                providers.extend(
                    provider
                    for provider in ("genius", "azlyrics")
                    if provider not in providers
                )
            if providers:
                args.extend(["--lyrics", *providers])
                if (
                    job.generate_lrc
                    and "--generate-lrc" in supported
                    and "synced" in providers
                ):
                    args.append("--generate-lrc")
        if (
            job.job_type == "sync"
            and job.sync_mode == "mirror"
            and job.sync_remove_lrc
            and "--sync-remove-lrc" in supported
        ):
            args.append("--sync-remove-lrc")
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
                data = parser.parse(line)
                if "progress" in data:
                    job.progress = data["progress"]
                if "current" in data:
                    job.current_track = data["current"]
                if "total" in data:
                    job.total = data["total"]
                if "completed" in data:
                    job.completed = data["completed"]
                if "phase" in data:
                    job.phase = data["phase"]
                if "failed" in data:
                    job.failed += data["failed"]
                self._emit("output", job, data)
            returncode = job.process.wait()
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
                            str(job.sync_file)
                            if job.sync_file and job.sync_file.exists()
                            else None
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
                job.lyrics_mode,
                job.lyrics_fallback,
                job.generate_lrc,
                ",".join(job.lyrics_providers),
                job.sync_remove_lrc,
            )
        if not self.callback:
            return
        payload = data or {}
        # Every callback enters GTK's main loop, including progress and logs.
        GLib.idle_add(self.callback, event, job, payload)

    def install_spotdl(self, callback: EventCallback | None = None):
        """Install spotDL into the app-managed venv after explicit UI consent."""
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
                emit("dependency-started", {"status": status})
                if install_spotdl and not status.spotdl:
                    venv = self.resolver.venv_dir
                    venv.parent.mkdir(parents=True, exist_ok=True)
                    run_command(
                        self.resolver.installation_command(),
                        "Creating private Python environment",
                    )
                    python = venv / "bin" / "python"
                    run_command(
                        [str(python), "-m", "pip", "install", "--upgrade", "spotdl"],
                        "Installing spotDL",
                    )
                    self.resolver.invalidate()
                    status = self.resolver.dependency_status()
                elif install_spotdl:
                    emit(
                        "dependency-output",
                        {
                            "line": "spotDL is already available; keeping the existing installation."
                        },
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

        threading.Thread(
            target=worker, daemon=True, name="groovia-dependency-install"
        ).start()

    def cancel_dependency_installation(self) -> bool:
        if self._dependency_process is None:
            return False
        self._dependency_cancel_requested = True
        try:
            self._dependency_process.terminate()
        except OSError:
            pass
        return True

    def remove_managed_dependencies(self) -> list[str]:
        """Remove only Groovia's private venv and spotDL tool home."""
        removed = []
        for path in self.resolver.managed_dependency_paths():
            if path.exists():
                shutil.rmtree(path)
                removed.append(str(path))
        self.resolver.invalidate()
        return removed
