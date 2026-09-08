# Linux Development

Linux is Groovia's primary supported platform.

The Meson, GNOME and Flatpak builds remain the reference implementations and
retain XDG paths, GSettings, GTK resources and MPRIS integration.

## Runtime dependencies

The runtime needs:

- GTK 4
- Libadwaita
- PyGObject
- GStreamer
- GStreamer base plugins
- GStreamer good plugins
- GStreamer bad plugins
- NumPy
- SciPy

Install your distribution's PyGObject, GTK and GStreamer packages plus
`python3-numpy` and `python3-scipy`, or the equivalent package names for your
distribution.

Meson checks both NumPy and SciPy at configure time.

## Build

```sh
meson setup build
meson compile -C build
./build/src/groovia
```

## Auto DJ tempo support

The optional GStreamer `pitch` / Rubber Band element enables
pitch-preserving tempo matching.

Without it, Auto DJ falls back to phrase-aware transitions.

## Flatpak

The Flatpak manifest installs pinned CPython 3.13 NumPy/SciPy wheels from
declared, SHA-256-verified sources with `pip --no-index`. It never contacts
PyPI during the module build.

Both `x86_64` and `aarch64` wheels are declared.

The GNOME 50 runtime supplies FFmpeg, FFprobe and GStreamer's `scaletempo`
element. This gives the sandbox both the bounded audio analysis tools and a
tempo-matching fallback without downloading unpinned build sources.

Groovia logs:

- the exact GStreamer version;
- searched element names;
- plugin filename;
- registry paths;
- plugin environment.

`rubberband` and `pitch` are optional. Groovia accepts either one when present
and otherwise uses the runtime's `scaletempo` element.

The Flatpak requests network access and scoped read/write access to the user's
Music directory. No home-directory permission is used.

You can also use this command to install the flatpak with `flatpak-builder`
```bash
flatpak-builder --user --install --force-clean \
  build-flatpak \
  io.github.Lluciocc.Groovia.json
```
