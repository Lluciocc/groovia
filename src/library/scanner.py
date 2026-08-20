# scanner.py
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

import hashlib
import threading
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")
from gi.repository import GLib, Gst

Gst.init(None)

from ..models import Track
from ..platform_compat import get_cache_dir

FORMATS = {".mp3", ".flac", ".ogg", ".oga", ".opus", ".wav", ".aac", ".m4a", ".mp4"}


class LibraryScanner:
    def __init__(self, database):
        self.database = database
        self.artwork_dir = get_cache_dir() / "artwork"
        self.artwork_dir.mkdir(parents=True, exist_ok=True)

    def scan_async(self, folders: list[str], callback):
        def worker():
            paths = [
                p
                for folder in folders
                for p in Path(folder).rglob("*")
                if p.is_file() and p.suffix.lower() in FORMATS
            ]
            total = len(paths)
            tracks = []
            for index, path in enumerate(paths, 1):
                tracks.append(self._read_track(path))
                GLib.idle_add(callback, "progress", index, total)
            if tracks:
                self.database.upsert_tracks(tracks)
            # Resolve the rows again so enrichment receives the final
            # persisted metadata/id, just like the download importer does.
            stored_tracks = [self.database.track_by_path(track.path) or track for track in tracks]
            GLib.idle_add(callback, "finished", len(tracks), total, stored_tracks)

        threading.Thread(target=worker, daemon=True, name="library-scan").start()

    def _read_track(self, path: Path) -> Track:
        title = path.stem.replace("_", " ").replace("-", " – ")
        artist, album = "Unknown Artist", "Unknown Album"
        album_artist, year = "", ""
        track_number, disc_number = 0, 1
        parts = path.stem.split(" - ", 1)
        if len(parts) == 2:
            artist, title = parts[0].strip(), parts[1].strip()
        cover = next(
            (
                str(path.parent / name)
                for name in ("cover.jpg", "cover.png", "folder.jpg", "folder.png")
                if (path.parent / name).exists()
            ),
            None,
        )
        duration = 0.0
        try:
            discoverer = GstPbutils.Discoverer.new(2 * Gst.SECOND)
            info = discoverer.discover_uri(path.as_uri())
            duration = info.get_duration() / Gst.SECOND
            tags = info.get_tags()
            if tags:

                def tag(name, default):
                    ok, value = tags.get_string(name)
                    return value if ok and value else default

                def number_tag(name, default):
                    try:
                        ok, value = tags.get_uint(name)
                        return int(value) if ok and value is not None else default
                    except (AttributeError, TypeError, ValueError):
                        return default

                title, artist, album = (
                    tag("title", title),
                    tag("artist", artist),
                    tag("album", album),
                )
                album_artist = tag("album-artist", artist)
                track_number = number_tag("track-number", 0)
                disc_number = number_tag("album-disc-number", 1)
                for date_tag in ("datetime", "date-time", "date"):
                    try:
                        ok, value = tags.get_date_time(date_tag)
                        if ok and value:
                            year = str(value.get_year())
                            break
                    except (AttributeError, TypeError, ValueError):
                        continue
                if not cover:
                    cover = self._extract_embedded_cover(tags, path)
        except Exception:
            pass
        return Track(
            None,
            title,
            artist,
            album,
            album_artist or artist,
            year,
            "",
            track_number,
            disc_number,
            duration,
            str(path),
            cover,
        )

    def read_track(self, path: str) -> Track:
        """Read one file on demand, useful for libraries imported before artwork support."""
        return self._read_track(Path(path))

    def read_embedded_lyrics(self, path: str) -> str | None:
        """Read plain lyrics exposed by GStreamer without altering the audio file."""
        try:
            discoverer = GstPbutils.Discoverer.new(5 * Gst.SECOND)
            info = discoverer.discover_uri(Path(path).resolve().as_uri())
            tags = info.get_tags()
            if not tags:
                return None
            for tag_name in ("lyrics", "unsynced-lyrics", "extended-comment"):
                try:
                    ok, value = tags.get_string(tag_name)
                    if ok and value:
                        return value
                except Exception:
                    continue
        except Exception:
            return None
        return None

    def inspect_track(self, path: str) -> dict[str, str]:
        """Return presentation-friendly technical metadata for Song Information."""
        details = {
            "codec": "Unknown",
            "bitrate": "Unknown",
            "sample_rate": "Unknown",
            "channels": "Unknown",
        }
        if not GstPbutils:
            return details
        try:
            discoverer = GstPbutils.Discoverer.new(5 * Gst.SECOND)
            info = discoverer.discover_uri(Path(path).resolve().as_uri())
            streams = info.get_audio_streams()
            if not streams:
                return details
            stream = streams[0]
            caps = stream.get_caps()
            if caps and caps.get_size():
                details["codec"] = caps.get_structure(0).get_name()
            sample_rate = stream.get_sample_rate()
            bitrate = stream.get_bitrate()
            channels = stream.get_channels()
            if sample_rate:
                details["sample_rate"] = f"{sample_rate:,} Hz"
            if bitrate:
                details["bitrate"] = f"{bitrate / 1000:.0f} kbps"
            if channels:
                details["channels"] = str(channels)
        except Exception:
            pass
        return details

    def _extract_embedded_cover(self, tags, path: Path) -> str | None:
        """Persist GstTagList's image sample so GTK can display it like any other cover."""
        if not GstPbutils:
            return None
        for tag_name in ("image", "preview-image"):
            try:
                if tags.get_tag_size(tag_name) == 0:
                    continue
                # GStreamer exposes artwork as a GstSample. get_sample() is
                # required for ID3/MP4 images; get_value_index() can return a
                # boxed value that cannot be painted by GTK.
                result = tags.get_sample(tag_name)
                if isinstance(result, tuple):
                    found, sample = result
                    if not found:
                        sample = None
                else:
                    sample = result
                if sample is None:
                    result = tags.get_sample_index(tag_name, 0)
                    if isinstance(result, tuple):
                        found, sample = result
                        if not found:
                            sample = None
                    else:
                        sample = result
                if sample is None:
                    continue
                buffer = sample.get_buffer()
                ok, mapped = buffer.map(Gst.MapFlags.READ)
                if not ok:
                    continue
                digest = hashlib.sha1(f"{path}:{path.stat().st_mtime_ns}".encode()).hexdigest()
                suffix = (
                    ".png"
                    if "png" in (sample.get_caps().to_string() if sample.get_caps() else "")
                    else ".jpg"
                )
                destination = self.artwork_dir / f"{digest}{suffix}"
                if not destination.exists():
                    destination.write_bytes(mapped.data)
                buffer.unmap(mapped)
                return str(destination)
            except Exception:
                continue
        return None


try:
    from gi.repository import GstPbutils
except (ImportError, ValueError):
    GstPbutils = None
