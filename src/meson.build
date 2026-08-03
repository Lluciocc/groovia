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

conf = configuration_data()
conf.set('PYTHON', python.find_installation('python3').full_path())
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
  'visuals.py',
]

install_data(groovia_sources, install_dir: moduledir)
install_data(['audio/__init__.py', 'audio/player.py'], install_dir: moduledir / 'audio')
install_data(['library/__init__.py', 'library/database.py', 'library/scanner.py'], install_dir: moduledir / 'library')
install_data(['widgets/__init__.py', 'widgets/vinyl_view.py'], install_dir: moduledir / 'widgets')
install_data(['downloads/__init__.py', 'downloads/manager.py'], install_dir: moduledir / 'downloads')
