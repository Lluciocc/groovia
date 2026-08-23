<img height="128" src="data/icons/hicolor/scalable/apps/io.github.Lluciocc.Groovia.svg" align="left"/>

# Groovia

Your local music, beautifully organized.

Groovia is a music player for GNOME focused on local music libraries.

The app includes an album and artist library, queue, lyrics, an animated vinyl player and an optional **Auto DJ** mode.

## Screenshots
<img width="2557" height="1554" alt="image" src="https://github.com/user-attachments/assets/d818af97-1e20-4f64-83f2-f4f46099a507" />

<img width="2557" height="1554" alt="image" src="https://github.com/user-attachments/assets/786e458a-9d5d-4446-96b4-8db511d3e705" />

<img width="2556" height="1554" alt="infinite" src="https://github.com/user-attachments/assets/c9f25a22-6e0b-4cca-a710-ad12fc7c2e2e" />


## Why Groovia?

Groovia is built for people who still keep and enjoy their own music collection.

It combines a clean, album-focused library with a playful vinyl-inspired interface, while keeping your music local and under your control. There are no accounts or subscriptions: Groovia helps you rediscover the music you already own.

Groovia is still growing, but the goal is simple: make listening to a local music library feel personal, modern and enjoyable again.

## What is Auto DJ?

One of Groovia's key features is **Auto DJ**, an experimental mixing mode.

Instead of simply stopping one track and starting the next, it analyzes your music to create smoother transitions between songs. Groovia can adjust the tempo and pitch, choose a suitable transition point and crossfade both tracks to make the queue feel more continuous.

It is not intended to replace a real DJ or produce a perfect mix every time. It is a playful way to rediscover your library and enjoy transitions that feel more natural than standard playback.

Auto DJ is disabled by default because it can be demanding on system resources. 

You can enable it from **Preferences → Auto DJ**, and doing so is **highly recommended** to enjoy the full Groovia experience !!

## Development

### Built with

- Python
- GTK 4
- Libadwaita
- GStreamer
- SQLite

The project is intended to be built with GNOME Builder or Meson.

```sh
meson setup build
meson compile -C build
./build/src/groovia
```

Linux is Groovia's primary supported platform.

For detailed setup and packaging instructions, see:

- [Linux development](docs/linux-development.md)
- [Windows development and packaging](docs/windows-development.md)
- [Spotify imports](docs/spotify-imports.md)
- [Auto DJ](docs/auto-dj.md)

## License
This project is licensed under the GPL3 License. See the [LICENSE file](COPYING) for details

## Support
If you like my work, please consider buying me a coffee :)

<a href="https://buymeacoffee.com/lluciocc" target="_blank">
    <img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee" height="50">
</a>
