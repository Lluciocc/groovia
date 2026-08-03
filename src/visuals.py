import math
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gst", "1.0")
from gi.repository import GdkPixbuf, GLib, Gst

Gst.init(None)


FALLBACK_PALETTE = ((0.98, 0.39, 0.30), (0.12, 0.10, 0.16))


def load_scaled_pixbuf(path: str, width: int = 64, height: int = 64):
    """Decode artwork through GStreamer and return a bounded RGB pixbuf.

    GNOME Platform versions that use Glycin can fail in GdkPixbuf's legacy
    loader process for otherwise valid embedded JPEGs. GStreamer already has
    the image decoders used by the audio stack, and decoding into a small
    appsink also keeps artwork work bounded before it reaches Cairo.
    """
    source = Gst.ElementFactory.make("uridecodebin")
    convert = Gst.ElementFactory.make("videoconvert")
    scale = Gst.ElementFactory.make("videoscale")
    caps = Gst.ElementFactory.make("capsfilter")
    sink = Gst.ElementFactory.make("appsink")
    if not all((source, convert, scale, caps, sink)):
        raise RuntimeError("GStreamer image decoding is unavailable")

    source.props.uri = Gst.filename_to_uri(str(Path(path).resolve()))
    caps.props.caps = Gst.Caps.from_string(
        f"video/x-raw,format=RGB,width={int(width)},height={int(height)},pixel-aspect-ratio=1/1"
    )
    sink.props.sync = False
    sink.props.max_buffers = 1
    sink.props.drop = True

    pipeline = Gst.Pipeline.new(None)
    for element in (source, convert, scale, caps, sink):
        pipeline.add(element)
    if not convert.link(scale) or not scale.link(caps) or not caps.link(sink):
        pipeline.set_state(Gst.State.NULL)
        raise RuntimeError("Could not link GStreamer image decoder")

    def on_pad_added(_source, pad):
        target = convert.get_static_pad("sink")
        if not target.is_linked() and pad.can_link(target):
            pad.link(target)

    source.connect("pad-added", on_pad_added)
    try:
        pipeline.set_state(Gst.State.PAUSED)
        _, state, _ = pipeline.get_state(3 * Gst.SECOND)
        if state not in (Gst.State.PAUSED, Gst.State.PLAYING):
            raise RuntimeError(f"Could not decode artwork: {path}")
        sample = sink.emit("try-pull-preroll", 3 * Gst.SECOND)
        if sample is None:
            raise RuntimeError(f"Could not decode artwork: {path}")
        info = sample.get_caps().get_structure(0)
        decoded_width = info.get_value("width")
        decoded_height = info.get_value("height")
        buffer = sample.get_buffer()
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if not ok:
            raise RuntimeError(f"Could not read decoded artwork: {path}")
        try:
            pixels = bytes(mapped.data)
        finally:
            buffer.unmap(mapped)
        return GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(pixels), GdkPixbuf.Colorspace.RGB, False, 8,
            decoded_width, decoded_height, decoded_width * 3
        )
    finally:
        pipeline.set_state(Gst.State.NULL)

def palette_for(path: str | None, cache: dict) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return an accent/background pair, cached so album changes do not resample every frame."""
    if not path or not Path(path).exists():
        print("No album art found, using fallback palette.")
        return FALLBACK_PALETTE
    try:
        stat = Path(path).stat()
        key = f"{path}:{stat.st_mtime_ns}:{stat.st_size}"
        print(f"Sampling palette for {path} (key: {key})")

        if key in cache:
            return cache[key]
        print(f"Palette image path: {path!r}")
        print(f"Exists: {Path(path).exists() if path else False}")
        print(f"Resolved: {Path(path).resolve() if path else None}")
        pixbuf = load_scaled_pixbuf(path, 64, 64)
        pixels = pixbuf.get_pixels()
        channels = pixbuf.get_n_channels()
        rowstride = pixbuf.get_rowstride()
        candidates = []
        for y in range(0, pixbuf.get_height(), 3):
            for x in range(0, pixbuf.get_width(), 3):
                offset = y * rowstride + x * channels
                r, g, b = (pixels[offset + i] / 255 for i in range(3))
                brightness = (r + g + b) / 3
                saturation = max(r, g, b) - min(r, g, b)
                if 0.08 < brightness < 0.94:
                    candidates.append((saturation * (1 - abs(brightness - .5)), r, g, b))
        if not candidates:
            print("No suitable colors found, using fallback palette.")
            return FALLBACK_PALETTE
        candidates.sort(reverse=True)
        _, r, g, b = candidates[min(3, len(candidates) - 1)]
        # Keep accents legible on dark GNOME surfaces.
        lift = max(0.0, .42 - (r + g + b) / 3)
        accent = (min(1, r + lift), min(1, g + lift), min(1, b + lift))
        background = tuple(max(0.035, value * .22) for value in (r, g, b))
        cache[key] = (accent, background)
        return cache[key]
    except Exception as e:
        print(f"An error occurred while sampling the palette, using fallback palette: {e}")
        return FALLBACK_PALETTE


def mix(first, second, progress: float):
    progress = max(0, min(1, progress))
    # Smoothstep gives the accent change a soft, non-linear arrival.
    progress = progress * progress * (3 - 2 * progress)
    return tuple(a + (b - a) * progress for a, b in zip(first, second))


def css_rgb(color):
    return "rgb(%d,%d,%d)" % tuple(round(max(0, min(1, value)) * 255) for value in color)
