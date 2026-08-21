# Groovia macOS packaging

This directory implements the experimental native macOS port. The first build
target is Apple Silicon (`arm64`) on macOS 13 or newer. The scripts accept
`GROOVIA_MACOS_ARCH=x86_64` or `universal2` so future work has an explicit
path, but those outputs are not currently claimed or published.

The bundle uses PyInstaller in `onedir`/windowed mode. PyInstaller provides the
Python bootloader and Apple bundle layout; `Groovia.spec` explicitly collects
PyGObject, NumPy, SciPy, typelibs, schemas, icon themes, GStreamer plugins and
the plugin scanner. `repair-python-framework.py` restores the interpreter's
versioned framework tree with symlinks preserved after PyInstaller.
`relocate-macho.py` copies and rewrites remaining Homebrew Mach-O dependencies.
`validate-bundle.py` then rejects all absolute Homebrew
references. This layered approach is more inspectable than relying on
automatic PyInstaller hooks alone.

## Build

```sh
packaging/macos/install-dependencies.sh
source .venv-macos/bin/activate
meson setup build/macos-dev
meson compile -C build/macos-dev
mkdir -p build/macos-dev/schemas
glib-compile-schemas --strict --targetdir build/macos-dev/schemas data
GROOVIA_RESOURCE_DIR="$PWD/build/macos-dev/src" \
  GSETTINGS_SCHEMA_DIR="$PWD/build/macos-dev/schemas" \
  packaging/macos/diagnose.py
packaging/macos/build-app.sh
packaging/macos/validate-bundle.py dist/Groovia.app
packaging/macos/sign-app.sh --adhoc dist/Groovia.app
codesign --verify --deep --strict --verbose=4 dist/Groovia.app
packaging/macos/create-dmg.sh
```

Only `build/macos` is recursively cleaned. An existing `dist/Groovia.app` is
moved into that build directory before the new validated product is installed.
The source logo is rendered into every required iconset size and converted with
Apple's `iconutil`; there is no replacement artwork.

`dependencies.json` is the single source for the target and Python package
versions. Homebrew formula names are fixed, while formula revisions are those
available from the installed Homebrew repository. Archive exact Homebrew bottle
metadata in a release build if bit-for-bit reproducibility becomes a release
requirement.

Optional spotDL, FFmpeg and Deno binaries are deliberately not downloaded by
the build. Their manifest entries are `null`; a future bundle must add upstream
versions, URLs, licenses and SHA-256 values before enabling such downloads.
Development mode and the app both support tools found in PATH, and runtime code
already detects a private `Contents/Resources/tools` directory if a controlled
future build stages one.

## Signing

An ad-hoc signature is applied after Mach-O relocation:

```sh
packaging/macos/sign-app.sh --adhoc dist/Groovia.app
```

For distribution, first import a Developer ID certificate into the login or CI
keychain, then sign in dependency-first order:

```sh
packaging/macos/sign-app.sh --identity dist/Groovia.app \
  "Developer ID Application: Example Name (TEAMID)"
```

Notarization supports either an existing notarytool keychain profile or the
three environment variables shown below. Never commit their values.

```sh
export APPLE_NOTARY_PROFILE=groovia-notary
packaging/macos/notarize.sh dist/Groovia.app

# Alternative:
export APPLE_ID='developer@example.com'
export APPLE_TEAM_ID='TEAMID'
export APPLE_APP_PASSWORD='app-specific-password'
packaging/macos/notarize.sh dist/Groovia.app
```

The signing script signs Mach-O children and nested bundles before the outer
application; it does not use an uncontrolled `codesign --deep` operation.

## Validation split

The default validator is headless. It checks the layout and plist, every Mach-O
architecture and dependency, required resources and typelibs, the schema, core
GStreamer factories, locally generated WAV decoding, macOS data paths, and a
session with an intentionally invalid D-Bus address. Use `--gui` only in a
logged-in WindowServer session:

```sh
packaging/macos/validate-bundle.py --gui dist/Groovia.app
```

The `--gui` probe only confirms that the process remains alive. Library import,
real audio output, Finder selection, menus, shortcuts, drag-and-drop, sleep/
wake, and visual behavior still require the manual checklist in
`docs/macos-development.md`.
