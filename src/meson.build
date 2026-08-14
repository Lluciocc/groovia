# meson.build
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

pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()
moduledir = pkgdatadir / 'groovia'
gnome = import('gnome')

gnome.compile_resources('groovia',
  'groovia.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
)

python = import('python')
python_exe = python.find_installation('python3', required: true)
python_check = run_command(python_exe, '-c', 'import numpy, scipy', check: false)
if python_check.returncode() != 0
  error('Auto DJ requires NumPy and SciPy. Install the platform DSP packages before configuring Groovia.')
endif

conf = configuration_data()
conf.set('PYTHON', python_exe.full_path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', get_option('prefix') / get_option('localedir'))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'groovia.in',
  output: 'groovia',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x'
)

groovia_sources = [
  '__init__.py',
  'main.py',
  'window.py',
  'models.py',
  'preferences.py',
  'mpris.py',
  'platform_compat.py',
  'logging_utils.py',
  'runtime.py',
  'visuals.py',
]

install_data(groovia_sources, install_dir: moduledir)
install_data(['audio/__init__.py', 'audio/player.py'], install_dir: moduledir / 'audio')
install_data(['autodj/__init__.py', 'autodj/analysis.py', 'autodj/planner.py', 'autodj/service.py'], install_dir: moduledir / 'autodj')
install_data(['library/__init__.py', 'library/database.py', 'library/scanner.py'], install_dir: moduledir / 'library')
install_data(['widgets/__init__.py', 'widgets/vinyl_view.py', 'widgets/lyrics_view.py'], install_dir: moduledir / 'widgets')
install_data([
  'lyrics/__init__.py', 'lyrics/artwork.py', 'lyrics/better_lyrics.py', 'lyrics/lrclib.py', 'lyrics/parser.py', 'lyrics/service.py',
], install_dir: moduledir / 'lyrics')
install_data([
  'downloads/__init__.py', 'downloads/manager.py', 'downloads/spotdl.py',
  'downloads/importer.py', 'downloads/service.py',
], install_dir: moduledir / 'downloads')
