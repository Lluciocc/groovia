# Groovia

Groovia is a modern GNOME music player for local files, built with Python,
GTK 4, Libadwaita, GStreamer and SQLite. It presents a local collection as
an album-first library with an animated vinyl deck, queue and playback bar.

## Development

The project is intended to be built with GNOME Builder or Meson. The runtime
needs GTK 4, Libadwaita, PyGObject and the GStreamer base/good/bad plugins.

```sh
meson setup build
meson compile -C build
./build/src/groovia
```

The interface is intentionally English for this first release.
