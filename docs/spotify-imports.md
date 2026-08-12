# Spotify Imports

Groovia uses the official spotDL command-line tool to find matching audio on
external providers and import Spotify metadata and artwork.

Audio is **not downloaded directly from Spotify**.

Users are responsible for copyright compliance and applicable service terms.

## Linux

On Linux, the first import asks for confirmation before installing spotDL in
Groovia's private environment:

```text
$XDG_DATA_HOME/groovia/downloader/venv
```

The existing Linux FFmpeg and Deno management flow remains unchanged.

## Windows

On Windows, the installer supplies the native executables under:

```text
{app}\tools
```

The Preferences page only verifies these tools.

The Inno Setup uninstaller owns packaged files.

Music, database, lyrics, cache and configuration remain outside `{app}`.

## Download destinations

Spotify-imported files are written to:

```text
Music/Groovia
```

Synchronized playlists are written to:

```text
Music/Groovia/Synced Playlists
```

## Lyrics

Lyrics are optional.

spotDL is used only for audio download and playlist synchronization. Lyrics
are fetched independently from track metadata, with Better Lyrics as the
primary online provider and LRCLIB as the fallback.

Groovia keeps lyrics mappings in SQLite.

Manually imported lyrics are stored under:

```text
$XDG_DATA_HOME/groovia/lyrics
```

Edited lyrics are preserved during later downloads and synchronization.
