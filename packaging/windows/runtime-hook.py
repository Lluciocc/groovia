# runtime-hook.py
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

import os
import sys
from pathlib import Path

bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))

typelib_dir = bundle_root / "typelibs"
if typelib_dir.is_dir():
    os.environ["GI_TYPELIB_PATH"] = str(typelib_dir)

schema_dir = bundle_root / "schemas"
if (schema_dir / "gschemas.compiled").is_file():
    os.environ["GSETTINGS_SCHEMA_DIR"] = str(schema_dir)

plugin_dir = bundle_root / "gstreamer-1.0"
if plugin_dir.is_dir():
    os.environ["GST_PLUGIN_PATH_1_0"] = str(plugin_dir)
    os.environ["GST_PLUGIN_PATH"] = str(plugin_dir)
    os.environ["GST_PLUGIN_SYSTEM_PATH_1_0"] = ""

    scanner = plugin_dir / "gst-plugin-scanner.exe"
    if scanner.is_file():
        os.environ["GST_PLUGIN_SCANNER"] = str(scanner)
