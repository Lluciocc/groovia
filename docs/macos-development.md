# Experimental macOS development

Groovia keeps its GTK 4 and Libadwaita interface on macOS. It is not an AppKit
rewrite. Linux remains the reference platform, and macOS support is
experimental until a bundle has passed the manual checklist below on physical
hardware.

## Supported target

The first official target is Apple Silicon `arm64`, built natively on macOS 13
or newer. The packaging interfaces accept `x86_64` and `universal2`, and
PyInstaller can validate those slices when every dependency provides them.
Neither Intel nor universal artifacts are currently supplied or claimed.

Build on the oldest macOS release that you intend to support. Homebrew and
PyInstaller binaries inherit their build host's deployment constraints, so a
bundle produced on a newer release cannot prove compatibility with an older
one.

## Install Homebrew and dependencies

Install Homebrew from <https://brew.sh>, then run:

```sh
packaging/macos/install-dependencies.sh
source .venv-macos/bin/activate
```

The script reads `packaging/macos/dependencies.json`, installs the declared
GTK, Libadwaita, GStreamer and build formulae, and creates a pinned Python 3.13
environment. Homebrew formula revisions follow the checked-out Homebrew
repository; Python package versions are exact.

No FFmpeg, Deno or spotDL executable is fetched by the bundle build. In
development, install optional tools explicitly when needed:

```sh
brew install ffmpeg deno
python -m pip install spotdl
```

The private spotDL environment remains supported at:

```text
~/Library/Application Support/Groovia/downloader/venv/bin/python
~/Library/Application Support/Groovia/downloader/venv/bin/spotdl
```

## Run from source

Compile the resource and schema first:

```sh
source .venv-macos/bin/activate
meson setup build/macos-dev
meson compile -C build/macos-dev
mkdir -p build/macos-dev/schemas
glib-compile-schemas --strict --targetdir build/macos-dev/schemas data
GROOVIA_RESOURCE_DIR="$PWD/build/macos-dev/src" \
GSETTINGS_SCHEMA_DIR="$PWD/build/macos-dev/schemas" \
python -m src.main
```

Groovia uses the native macOS locations:

| Purpose | Location |
| --- | --- |
| Persistent data | `~/Library/Application Support/Groovia` |
| Cache | `~/Library/Caches/Groovia` |
| Configuration | `~/Library/Preferences/Groovia` |
| Music | `~/Music` |

`GROOVIA_MUSIC_DIR` continues to override the default music location.

## Diagnostic

With the development schema available, run:

```sh
source .venv-macos/bin/activate
GROOVIA_RESOURCE_DIR="$PWD/build/macos-dev/src" \
GSETTINGS_SCHEMA_DIR="$PWD/build/macos-dev/schemas" \
python packaging/macos/diagnose.py
```

The diagnostic reports architecture; Python, GTK, Libadwaita and GStreamer
versions; NumPy and SciPy; FFmpeg, FFprobe, spotDL and Deno; every important
GStreamer factory; scanner, schema, typelib and plugin paths; and Groovia's
data directories. It returns nonzero only for launch/basic-playback
requirements. Optional download tools, effects and tempo filters are warnings.

Required factories are `playbin`, `audioconvert` and `audioresample`. Auto DJ
can additionally use `equalizer-3bands`, `audioecho` and `freeverb`. Tempo
matching needs one of `rubberband`, `pitch` or `scaletempo`. If none exists,
normal playback remains available, the existing two-playbin crossfade remains
available, and Auto DJ uses transitions without tempo stretching. Preferences
and logs report this limitation.

## Build and validate Groovia.app

```sh
source .venv-macos/bin/activate
GROOVIA_MACOS_ARCH=arm64 packaging/macos/build-app.sh
python packaging/macos/validate-bundle.py dist/Groovia.app
```

The build cleans only the exact `build/macos` directory. It compiles the GTK
resource and GSettings schema, renders the maintained SVG into a true `.icns`,
collects Python and native runtimes, copies typelibs and GStreamer plugins,
relocates Homebrew Mach-O dependencies, applies an ad-hoc signature, and runs
the final validator before placing the result in `dist/Groovia.app`.

The validator checks structure and `Info.plist`, architecture slices, every
`otool -L` dependency, forbidden Homebrew paths, resources, schema, typelibs,
scanner and plugin discovery. Its bundle executable creates and decodes a WAV
fixture with GStreamer and writes temporary data through the expected macOS
paths. It supplies an invalid session D-Bus address, so the macOS path cannot
silently rely on MPRIS or `org.freedesktop.FileManager1`.

The normal check is headless. In a logged-in graphical session, add:

```sh
python packaging/macos/validate-bundle.py --gui dist/Groovia.app
```

That probe only checks that the application remains running for five seconds;
the full visual and audio-output checks remain manual.

## Create a DMG

```sh
GROOVIA_MACOS_ARCH=arm64 packaging/macos/create-dmg.sh dist/Groovia.app
```

The result is `dist/Groovia-<version>-macOS-arm64.dmg`, containing the app and
an Applications shortcut. The DMG is optional and does not imply signing or
notarization.

## Tests and lint

```sh
source .venv-macos/bin/activate
python -m pytest
python -m ruff check .
python -m isort --check-only src packaging/macos tests
meson test -C build/macos-dev --print-errorlogs
```

Linux CI can simulate Darwin path selection, Finder commands, `.app` resource
discovery, scanner naming, tool paths with spaces, MPRIS disablement and tempo
fallbacks. Only a real macOS runner can inspect Mach-O binaries and execute the
bundle.

## Ad-hoc signing

The build already ad-hoc signs after relocation. To repeat it:

```sh
packaging/macos/sign-app.sh --adhoc dist/Groovia.app
```

## Developer ID signing and notarization

Import a `Developer ID Application` certificate into a keychain, then run:

```sh
packaging/macos/sign-app.sh --identity dist/Groovia.app \
  "Developer ID Application: Your Name (TEAMID)"
codesign --verify --strict --verbose=2 dist/Groovia.app
```

The script signs leaf Mach-O files and nested bundles before the outer app. It
does not use `codesign --deep` as a substitute for controlled ordering.

Store notarization credentials in the keychain when possible:

```sh
xcrun notarytool store-credentials groovia-notary \
  --apple-id "developer@example.com" --team-id "TEAMID" --password "APP-PASSWORD"
APPLE_NOTARY_PROFILE=groovia-notary \
  packaging/macos/notarize.sh dist/Groovia.app
```

For CI, the same script accepts `APPLE_ID`, `APPLE_TEAM_ID` and
`APPLE_APP_PASSWORD`. GitHub Actions additionally expects these optional
secrets for Developer ID mode:

- `APPLE_CERTIFICATE_P12`: base64-encoded `.p12`;
- `APPLE_CERTIFICATE_PASSWORD`;
- `APPLE_SIGNING_IDENTITY`;
- `APPLE_KEYCHAIN_PASSWORD`;
- `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD` for notarization.

Unsigned/ad-hoc workflow artifacts require no secrets. Developer signing and
notarization only run for an explicitly dispatched workflow with every needed
secret. The workflow uploads artifacts and never publishes a GitHub Release.

## Known limits

- macOS support is experimental and has no stable-release claim yet.
- MPRIS is Linux-only. There is no current macOS Control Center backend; the
  platform layer exposes a clean media-backend boundary for future work.
- Downloader binaries are not embedded until pinned macOS URLs, versions,
  licenses and SHA-256 values are added deliberately.
- Tempo matching depends on available GStreamer plugins. Its absence is not a
  playback, crossfade or Auto DJ failure.
- GTK/Libadwaita retain Groovia's visual identity. Native window-control layout
  is requested only when the installed bindings expose it.

## Manual test checklist on a physical Mac

- [ ] Build natively on an Apple Silicon Mac and confirm every binary is arm64.
- [ ] Copy the app to a clean account without Homebrew or Python and launch it.
- [ ] Confirm first launch, relaunch, quit with Command+Q and Preferences with Command+,.
- [ ] Open About and Keyboard Shortcuts from both the app menu and Groovia menu.
- [ ] Confirm window controls, resize, fullscreen, multiple displays and Retina rendering.
- [ ] Import folders and drag audio files whose paths contain spaces and non-ASCII text.
- [ ] Play MP3, FLAC, Ogg/Vorbis, Opus, WAV, AAC and M4A through real audio output.
- [ ] Test seek, pause, volume, mute, next/previous, queue, gapless and crossfade.
- [ ] Test Auto DJ with and without a tempo plugin and inspect its preference diagnostic.
- [ ] Verify generated GStreamer registry and no dependency on Homebrew paths.
- [ ] Reveal a track in Finder and confirm the exact file is selected.
- [ ] Confirm no MPRIS or freedesktop file-manager D-Bus attempt appears in logs.
- [ ] Restart after sleep/wake and after disconnecting/reconnecting an audio device.
- [ ] Verify library database, artwork, lyrics and settings survive an app upgrade.
- [ ] Exercise synchronized lyrics, TheAudioDB and network-offline fallbacks.
- [ ] Test spotDL with PATH/Homebrew tools and the private `venv/bin/spotdl` flow.
- [ ] Mount the DMG, drag to Applications, eject it, and launch the installed copy.
- [ ] Verify ad-hoc behavior on a test machine and Gatekeeper behavior for notarized output.
- [ ] Repeat the headless validator and the `--gui` probe on the final signed app.
