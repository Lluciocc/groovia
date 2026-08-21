#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"
app="${1:-$repo_root/dist/Groovia.app}"
if [[ "$(uname -s)" != "Darwin" || ! -d "$app/Contents" ]]; then
  echo "A built Groovia.app and macOS are required." >&2
  exit 2
fi
version="$(tr -d '[:space:]' < "$repo_root/VERSION")"
arch="${GROOVIA_MACOS_ARCH:-arm64}"
output="$repo_root/dist/Groovia-${version}-macOS-${arch}.dmg"
dmg_root="$repo_root/build/macos/dmg"
if [[ "$dmg_root" != "$repo_root/build/macos/dmg" ]]; then
  echo "Refusing unsafe DMG cleanup: $dmg_root" >&2
  exit 2
fi
rm -rf -- "$dmg_root"
mkdir -p "$dmg_root"
ditto "$app" "$dmg_root/Groovia.app"
ln -s /Applications "$dmg_root/Applications"
hdiutil create -volname "Groovia" -srcfolder "$dmg_root" -ov -format UDZO "$output"
hdiutil verify "$output"
echo "Created: $output"
