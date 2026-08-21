#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"
manifest="$script_dir/dependencies.json"
venv="$repo_root/.venv-macos"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This dependency installer must run on macOS." >&2
  exit 2
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required: https://brew.sh" >&2
  exit 2
fi
if ! xcrun --find clang >/dev/null 2>&1; then
  echo "Install the Xcode Command Line Tools with: xcode-select --install" >&2
  exit 2
fi

formulae=()
while IFS= read -r formula; do
  formulae+=("$formula")
done < <(python3 - "$manifest" <<'PY'
import json, sys
for item in json.load(open(sys.argv[1], encoding="utf-8"))["homebrew_formulae"]:
    print(item)
PY
)
brew install "${formulae[@]}"

while IFS='=' read -r package minimum; do
  if ! pkg-config --atleast-version="$minimum" "$package"; then
    echo "$package >= $minimum is required by $manifest" >&2
    exit 1
  fi
done < <(python3 - "$manifest" <<'PY'
import json, sys
for package, version in json.load(open(sys.argv[1], encoding="utf-8"))["minimum_native_versions"].items():
    print(f"{package}={version}")
PY
)

brew_python="$(brew --prefix python@3.13)/bin/python3.13"
"$brew_python" -m venv "$venv"
"$venv/bin/python" -m pip install --upgrade pip
"$venv/bin/python" - "$manifest" <<'PY'
import json, subprocess, sys
versions = json.load(open(sys.argv[1], encoding="utf-8"))["python"]
packages = {
    "PyInstaller": versions["pyinstaller"],
    "PyGObject": versions["pygobject"],
    "numpy": versions["numpy"],
    "scipy": versions["scipy"],
    "pytest": versions["pytest"],
    "ruff": versions["ruff"],
    "isort": versions["isort"],
}
subprocess.run(
    [sys.executable, "-m", "pip", "install", *[f"{name}=={version}" for name, version in packages.items()]],
    check=True,
)
PY

echo "macOS development environment ready: $venv"
echo "Run: source .venv-macos/bin/activate"
