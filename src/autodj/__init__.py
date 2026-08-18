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

__all__ = [
    "AnalysisCache",
    "TrackAnalysis",
    "TrackAnalyzer",
    "TransitionPlan",
    "TransitionPlanner",
    "AutoDJService",
]

_EXPORTS = {
    "AnalysisCache": ("analysis", "AnalysisCache"),
    "TrackAnalysis": ("analysis", "TrackAnalysis"),
    "TrackAnalyzer": ("analysis", "TrackAnalyzer"),
    "TransitionPlan": ("planner", "TransitionPlan"),
    "TransitionPlanner": ("planner", "TransitionPlanner"),
    "AutoDJService": ("service", "AutoDJService"),
}


def __getattr__(name):
    """Avoid loading the analysis stack until Auto DJ is actually used."""
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _EXPORTS[name]
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value
