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

## Spotify imports

Groovia uses the official spotDL command-line tool to find matching audio on
external providers and import Spotify metadata and artwork. Audio is not
downloaded directly from Spotify; users are responsible for copyright and
service terms.

The first import asks before installing missing dependencies. spotDL is
installed in Groovia's private environment under
`$XDG_DATA_HOME/groovia/downloader/venv`; FFmpeg and Deno can be installed in
the same managed environment. No `sudo` or system-Python modification is used.
The Preferences > Downloads page shows installation progress and includes a
confirmation action to remove Groovia-managed spotDL, FFmpeg and Deno files;
system-wide installations are never removed. These files live below
`$XDG_DATA_HOME/groovia/downloader/`.

Spotify files are written to `Music/Groovia`, with synchronized playlists in
`Music/Groovia/Synced Playlists`. The Flatpak requests network access and
scoped read/write access to the user's Music directory so these configured
destinations work inside the sandbox; no home-directory permission is used.
