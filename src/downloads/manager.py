import os
import re
import threading
import urllib.parse
import urllib.request
from pathlib import Path

from gi.repository import GLib


class DownloadManager:
    """Downloads only user-supplied, direct audio URLs and reports progress on GTK's thread."""

    def __init__(self, callback):
        self.callback = callback

    def download(self, url: str):
        threading.Thread(target=self._worker, args=(url,), daemon=True, name="groovia-download").start()

    def _worker(self, url):
        try:
            parsed = urllib.parse.urlparse(url)
            name = Path(parsed.path).name or "downloaded-track"
            name = re.sub(r"[^\w. -]", "_", name)
            root = Path(os.environ.get("XDG_DOWNLOAD_DIR", Path.home() / "Downloads")) / "Groovia"
            root.mkdir(parents=True, exist_ok=True)
            destination = root / name
            request = urllib.request.Request(url, headers={"User-Agent": "Groovia/0.1"})
            with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as output:
                total = int(response.headers.get("Content-Length") or 0)
                downloaded = 0
                while chunk := response.read(1024 * 128):
                    output.write(chunk); downloaded += len(chunk)
                    GLib.idle_add(self.callback, "progress", downloaded, total)
            GLib.idle_add(self.callback, "finished", str(destination), 0)
        except Exception as error:
            GLib.idle_add(self.callback, "error", str(error), 0)
