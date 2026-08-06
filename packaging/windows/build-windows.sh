#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ps_args=()
for arg in "$@"; do
  case "$arg" in
    --skip-installer) ps_args+=(-SkipInstaller) ;;
    --console) ps_args+=(-Console) ;;
    *)
      echo "Usage: $0 [--skip-installer] [--console]" >&2
      exit 2
      ;;
  esac
done

powershell.exe -NoProfile -ExecutionPolicy Bypass \
  -File "$script_dir/build-windows.ps1" "${ps_args[@]}"
