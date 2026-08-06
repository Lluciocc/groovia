#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == "--skip-installer" ]]; then
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$script_dir/build-windows.ps1" -SkipInstaller
else
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$script_dir/build-windows.ps1"
fi
