# __init__.py
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

from importlib import import_module

_EXPORTS = {
    "DependencyStatus": ("spotdl", "DependencyStatus"),
    "DownloadJob": ("manager", "DownloadJob"),
    "DownloadManager": ("manager", "DownloadManager"),
    "ProgressParser": ("manager", "ProgressParser"),
    "SourceInfo": ("spotdl", "SourceInfo"),
    "SpotDLCommandResolver": ("spotdl", "SpotDLCommandResolver"),
    "SpotDLService": ("service", "SpotDLService"),
    "SpotDLUnavailable": ("spotdl", "SpotDLUnavailable"),
    "classify_input": ("spotdl", "classify_input"),
    "read_sync_source": ("spotdl", "read_sync_source"),
}


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _EXPORTS[name]
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value


__all__ = [
    "DependencyStatus",
    "DownloadJob",
    "DownloadManager",
    "ProgressParser",
    "SourceInfo",
    "SpotDLCommandResolver",
    "SpotDLService",
    "SpotDLUnavailable",
    "classify_input",
    "read_sync_source",
]
