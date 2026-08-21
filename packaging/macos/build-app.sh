#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"
build_root="$repo_root/build/macos"
stage="$build_root/stage/Resources"
package_root="$build_root/package/groovia"
pyinstaller_dist="$build_root/pyinstaller-dist"
output="$repo_root/dist/Groovia.app"
venv="${GROOVIA_MACOS_VENV:-$repo_root/.venv-macos}"
arch="${GROOVIA_MACOS_ARCH:-arm64}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Groovia.app can only be built and validated on macOS." >&2
  exit 2
fi
case "$arch" in arm64|x86_64|universal2) ;; *) echo "Unsupported architecture: $arch" >&2; exit 2 ;; esac
if [[ "$arch" == "arm64" && "$(uname -m)" != "arm64" ]]; then
  echo "The official arm64 build must run natively on Apple Silicon." >&2
  exit 2
fi
if [[ "$build_root" != "$repo_root/build/macos" || -z "$repo_root" ]]; then
  echo "Refusing unsafe macOS build cleanup: $build_root" >&2
  exit 2
fi
if [[ ! -x "$venv/bin/python" ]]; then
  echo "Missing $venv; run packaging/macos/install-dependencies.sh first." >&2
  exit 2
fi

rm -rf -- "$build_root"
mkdir -p "$stage/schemas" "$stage/typelibs" "$stage/gstreamer-1.0" \
  "$stage/libexec/gstreamer-1.0" "$stage/share/icons" "$stage/share/themes" \
  "$stage/locale" "$stage/tools" "$package_root" "$repo_root/dist"

export PATH="$(brew --prefix gettext)/bin:$(brew --prefix librsvg)/bin:$venv/bin:$PATH"
export PKG_CONFIG_PATH="$(brew --prefix libadwaita)/lib/pkgconfig:$(brew --prefix gtk4)/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-13.0}"
export GROOVIA_MACOS_ARCH="$arch"
export GROOVIA_MACOS_BUILD_DIR="$build_root"

glib-compile-resources --sourcedir "$repo_root/src" \
  --target "$stage/groovia.gresource" "$repo_root/src/groovia.gresource.xml"
glib-compile-schemas --strict --targetdir "$stage/schemas" "$repo_root/data"
cp "$repo_root/VERSION" "$stage/VERSION"
cp "$repo_root/COPYING" "$stage/COPYING"
rsync -a --exclude '__pycache__' --exclude '*.pyc' "$repo_root/src/" "$package_root/"
while IFS= read -r language; do
  [[ -z "$language" || "$language" == \#* ]] && continue
  catalog="$repo_root/po/$language.po"
  [[ -f "$catalog" ]] || { echo "Missing translation catalog: $catalog" >&2; exit 1; }
  destination="$stage/locale/$language/LC_MESSAGES"
  mkdir -p "$destination"
  msgfmt "$catalog" -o "$destination/groovia.mo"
done < "$repo_root/po/LINGUAS"

typelib_roots=()
while IFS= read -r formula; do
  prefix="$(brew --prefix "$formula")"
  root="$prefix/lib/girepository-1.0"
  [[ -d "$root" ]] && typelib_roots+=("$root")
done < <(brew list --formula)
if [[ "${#typelib_roots[@]}" -eq 0 ]]; then
  echo "No Homebrew GObject Introspection typelib directories found." >&2
  exit 1
fi
find -L "${typelib_roots[@]}" -type f -name '*.typelib' -print0 |
  while IFS= read -r -d '' typelib; do
    cp "$typelib" "$stage/typelibs/"
  done
if [[ -z "$(find "$stage/typelibs" -type f -name '*.typelib' -print -quit)" ]]; then
  echo "Homebrew GObject Introspection typelibs were not found." >&2
  exit 1
fi
for theme in Adwaita hicolor; do
  source_theme="$(brew --prefix)/share/icons/$theme"
  [[ -d "$source_theme" ]] && rsync -a "$source_theme/" "$stage/share/icons/$theme/"
done
for root in "$(brew --prefix)/share/themes" "$(brew --prefix gtk4)/share/themes"; do
  [[ -d "$root" ]] && rsync -a "$root/" "$stage/share/themes/"
done
mkdir -p "$stage/share/icons/hicolor/scalable/apps" "$stage/share/icons/hicolor/symbolic/apps"
cp "$repo_root/data/icons/hicolor/scalable/apps/io.github.Lluciocc.Groovia.svg" \
  "$stage/share/icons/hicolor/scalable/apps/"
cp "$repo_root/data/icons/hicolor/symbolic/apps/io.github.Lluciocc.Groovia-symbolic.svg" \
  "$stage/share/icons/hicolor/symbolic/apps/"

for formula in gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugins-ugly gst-libav; do
  prefix="$(brew --prefix "$formula")"
  [[ -d "$prefix/lib/gstreamer-1.0" ]] && rsync -a "$prefix/lib/gstreamer-1.0/" "$stage/gstreamer-1.0/"
done
scanner="$(find "$(brew --prefix)" -path '*/libexec/gstreamer-1.0/gst-plugin-scanner' -type f -print -quit)"
if [[ -z "$scanner" ]]; then
  echo "GStreamer plugin scanner not found in Homebrew." >&2
  exit 1
fi
cp "$scanner" "$stage/libexec/gstreamer-1.0/gst-plugin-scanner"
chmod +x "$stage/libexec/gstreamer-1.0/gst-plugin-scanner"

iconset="$build_root/Groovia.iconset"
mkdir -p "$iconset"
icon_source="$repo_root/data/icons/hicolor/scalable/apps/io.github.Lluciocc.Groovia.svg"
for size in 16 32 128 256 512; do
  rsvg-convert -w "$size" -h "$size" "$icon_source" -o "$iconset/icon_${size}x${size}.png"
  double=$((size * 2))
  rsvg-convert -w "$double" -h "$double" "$icon_source" -o "$iconset/icon_${size}x${size}@2x.png"
done
iconutil -c icns "$iconset" -o "$stage/Groovia.icns"

"$venv/bin/python" - "$script_dir/Info.plist.in" "$build_root/stage/Info.plist" \
  "$repo_root/VERSION" "$arch" "$MACOSX_DEPLOYMENT_TARGET" <<'PY'
import re, sys
template, output, version_file, arch, minimum = sys.argv[1:]
version = open(version_file, encoding="utf-8").read().strip()
numbers = re.findall(r"\d+", version)
build_version = ".".join((numbers + ["0", "0", "0"])[:3])
text = open(template, encoding="utf-8").read()
text = text.replace("@VERSION@", version).replace("@BUILD_VERSION@", build_version)
plist_arch = "arm64</string><string>x86_64" if arch == "universal2" else arch
text = text.replace("@ARCHITECTURE@", plist_arch).replace("@MINIMUM_MACOS@", minimum)
open(output, "w", encoding="utf-8").write(text)
PY

"$venv/bin/python" -m PyInstaller --noconfirm --clean \
  --distpath "$pyinstaller_dist" --workpath "$build_root/pyinstaller-work" \
  "$script_dir/Groovia.spec"
app="$pyinstaller_dist/Groovia.app"
[[ -d "$app" ]] || { echo "PyInstaller did not produce $app" >&2; exit 1; }

"$venv/bin/python" "$script_dir/relocate-macho.py" "$app"
"$venv/bin/python" "$script_dir/validate-bundle.py" "$app"

if [[ -e "$output" ]]; then
  mv "$output" "$build_root/previous-Groovia.app"
fi
mv "$app" "$output"
echo "Built and validated: $output"
