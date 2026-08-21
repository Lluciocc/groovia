from __future__ import annotations

import os
import re
import sys
import tempfile
import traceback
from pathlib import Path


def _resource_root() -> Path:
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        if parent.name == "Contents" and parent.parent.suffix == ".app":
            return parent / "Resources"
    return Path(getattr(sys, "_MEIPASS", executable.parent))


def _version() -> str:
    path = _resource_root() / "VERSION"
    version = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]*", version):
        raise RuntimeError(f"Invalid bundled VERSION: {version!r}")
    return version


def _smoke_test(write_test: bool = False) -> int:
    import gi
    import numpy
    import scipy

    gi.require_version("Gio", "2.0")
    gi.require_version("Gst", "1.0")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    # isort: off
    from gi.repository import Adw, GLib, Gst, Gtk
    from groovia.platform_compat import get_cache_dir, get_config_dir, get_data_dir, supports_mpris
    from groovia.runtime import create_settings
    # isort: on

    if supports_mpris():
        raise RuntimeError("MPRIS must remain disabled in a macOS bundle")
    settings = create_settings()
    settings.get_boolean("auto-dj-enabled")
    Gst.init(None)
    required = ("playbin", "audioconvert", "audioresample")
    missing = [name for name in required if Gst.ElementFactory.find(name) is None]
    if missing:
        raise RuntimeError("Missing required GStreamer elements: " + ", ".join(missing))
    optional = ("equalizer-3bands", "audioecho", "freeverb", "rubberband", "pitch", "scaletempo")
    optional_state = {name: Gst.ElementFactory.find(name) is not None for name in optional}

    with tempfile.TemporaryDirectory(prefix="groovia-audio-") as temporary:
        audio = Path(temporary) / "smoke.wav"
        writer = Gst.parse_launch(
            f'audiotestsrc num-buffers=32 ! audioconvert ! wavenc ! filesink location="{audio}"'
        )
        writer.set_state(Gst.State.PLAYING)
        message = writer.get_bus().timed_pop_filtered(
            10 * Gst.SECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS
        )
        writer.set_state(Gst.State.NULL)
        if message is None or message.type == Gst.MessageType.ERROR or not audio.is_file():
            raise RuntimeError("Could not generate the local GStreamer audio fixture")
        player = Gst.ElementFactory.make("playbin", None)
        sink = Gst.ElementFactory.make("fakesink", None)
        player.props.uri = GLib.filename_to_uri(str(audio), None)
        player.props.audio_sink = sink
        player.set_state(Gst.State.PLAYING)
        message = player.get_bus().timed_pop_filtered(
            10 * Gst.SECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS
        )
        player.set_state(Gst.State.NULL)
        if message is None or message.type == Gst.MessageType.ERROR:
            raise RuntimeError("Bundled playbin could not decode the generated WAV fixture")

    if write_test:
        for directory in (get_data_dir(), get_cache_dir(), get_config_dir()):
            directory.mkdir(parents=True, exist_ok=True)
            marker = directory / ".bundle-write-test"
            marker.write_text("ok\n", encoding="utf-8")
            marker.unlink()
    print(
        "Groovia bundle smoke test: "
        f"version={_version()} python={sys.version.split()[0]} "
        f"numpy={numpy.__version__} scipy={scipy.__version__} "
        f"gtk={Gtk.get_major_version()}.{Gtk.get_minor_version()} "
        f"libadwaita={Adw.get_major_version()}.{Adw.get_minor_version()} "
        f"gstreamer={Gst.version_string()} schema=io.github.Lluciocc.Groovia "
        f"optional={optional_state}"
    )
    return 0


def main() -> int:
    try:
        from groovia.runtime import initialize_runtime

        initialize_runtime(_resource_root())
        if "--bundle-smoke-test" in sys.argv:
            return _smoke_test("--write-test" in sys.argv)
        from groovia import main as groovia_main

        return int(groovia_main.main(_version()))
    except BaseException as error:
        traceback.print_exception(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
