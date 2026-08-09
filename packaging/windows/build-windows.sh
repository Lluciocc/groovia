#!/usr/bin/env bash
# build-windows.sh
#
# Copyright 2026 Lluciocc (llucio.cc00@gmail.com)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

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
