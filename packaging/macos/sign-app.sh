#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mode="${1:-}"
app="${2:-}"
if [[ "$mode" == "--adhoc" ]]; then
  identity="-"
  sign_options=(--timestamp=none)
elif [[ "$mode" == "--identity" && -n "${3:-}" ]]; then
  identity="$3"
  sign_options=(--timestamp --options runtime)
else
  echo "Usage: $0 --adhoc Groovia.app | --identity Groovia.app 'Developer ID Application: …'" >&2
  exit 2
fi
app="$(cd -- "$(dirname -- "$app")" && pwd)/$(basename -- "$app")"
if [[ ! -d "$app/Contents" || "$app" != *.app ]]; then
  echo "Invalid application bundle: $app" >&2
  exit 2
fi

while IFS= read -r -d '' item; do
  if file -b "$item" | grep -q 'Mach-O'; then
    codesign --force --sign "$identity" "${sign_options[@]}" "$item"
  fi
done < <(python3 - "$app" <<'PY'
import os, sys
from pathlib import Path
paths = [p for p in Path(sys.argv[1]).rglob("*") if p.is_file() and not p.is_symlink()]
for path in sorted(paths, key=lambda p: len(p.parts), reverse=True):
    os.write(1, os.fsencode(path) + b"\0")
PY
)

while IFS= read -r -d '' nested; do
  codesign --force --sign "$identity" "${sign_options[@]}" "$nested"
done < <(python3 - "$app" <<'PY'
import os, sys
from pathlib import Path
root = Path(sys.argv[1]) / "Contents"
paths = [
    path for path in root.rglob("*")
    if path.is_dir()
    and path.suffix in {".framework", ".app", ".xpc"}
    and path.name != "Python.framework"
]
for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
    os.write(1, os.fsencode(path) + b"\0")
PY
)

python3 - "$app" <<'PY'
import os
import sys
from pathlib import Path

framework = Path(sys.argv[1]) / "Contents" / "Frameworks" / "Python.framework"
versions = framework / "Versions"
current = versions / "Current"
if not versions.is_dir() or not current.is_symlink():
  raise SystemExit(f"Invalid Python.framework layout: {framework}")
version = current.resolve()
if version.parent != versions or not (version / "Python").is_file():
  raise SystemExit(f"Invalid Python.framework current version: {current}")
for name in ("Python", "Resources"):
  alias = framework / name
  expected = Path("Versions") / "Current" / name
  if not alias.is_symlink() or Path(os.readlink(alias)) != expected:
    raise SystemExit(f"Invalid Python.framework alias: {alias}")
PY

codesign --force --sign "$identity" "${sign_options[@]}" \
  "$app/Contents/Frameworks/Python.framework/Versions/Current/Python"
codesign --force --sign "$identity" "${sign_options[@]}" \
  "$app/Contents/Frameworks/Python.framework"

codesign --force --sign "$identity" "${sign_options[@]}" \
  --entitlements "$script_dir/entitlements.plist" "$app"
codesign --verify --deep --strict --verbose=4 "$app"
echo "Signed and verified $app with identity $identity"
