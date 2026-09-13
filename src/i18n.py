# i18n.py
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

from __future__ import annotations

import gettext as _gettext

DOMAIN = "groovia"


def gettext(message: str) -> str:
    return _gettext.dgettext(DOMAIN, message)


_ = gettext


def ngettext(singular: str, plural: str, count: int) -> str:
    return _gettext.dngettext(DOMAIN, singular, plural, count)


def pgettext(context: str, message: str) -> str:
    return _gettext.dpgettext(DOMAIN, context, message)
