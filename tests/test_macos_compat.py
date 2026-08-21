from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from src import platform_compat, runtime
from src.audio.capabilities import select_tempo_filter
from src.downloads import spotdl as spotdl_module


def configure_macos(monkeypatch, module=platform_compat):
    monkeypatch.setattr(module, "IS_WINDOWS", False)
    monkeypatch.setattr(module, "IS_LINUX", False)
    monkeypatch.setattr(module, "IS_MACOS", True)


def test_macos_constant_matches_sys_platform():
    assert platform_compat.IS_MACOS is (sys.platform == "darwin")


def test_macos_detection_can_be_simulated(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    location = Path(platform_compat.__file__)
    spec = importlib.util.spec_from_file_location("simulated_macos_platform", location)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.IS_MACOS is True


def test_macos_paths_and_explicit_music_override(monkeypatch, tmp_path):
    configure_macos(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("GROOVIA_MUSIC_DIR", raising=False)
    assert platform_compat.get_data_dir() == tmp_path / "Library/Application Support/Groovia"
    assert platform_compat.get_cache_dir() == tmp_path / "Library/Caches/Groovia"
    assert platform_compat.get_config_dir() == tmp_path / "Library/Preferences/Groovia"
    assert platform_compat.get_music_dir() == tmp_path / "Music"
    configured = tmp_path / "External Music"
    monkeypatch.setenv("GROOVIA_MUSIC_DIR", str(configured))
    assert platform_compat.get_music_dir() == configured


def test_managed_executable_names_are_posix_on_macos(monkeypatch):
    configure_macos(monkeypatch)
    assert platform_compat.get_managed_executable_name("ffmpeg") == "ffmpeg"
    assert platform_compat.get_managed_executable_name("gst-plugin-scanner") == "gst-plugin-scanner"


def test_simulated_app_resource_and_scanner_discovery(monkeypatch, tmp_path):
    contents = tmp_path / "Groovia With Spaces.app/Contents"
    executable = contents / "MacOS/Groovia"
    resources = contents / "Resources"
    executable.parent.mkdir(parents=True)
    executable.touch()
    (resources / "schemas").mkdir(parents=True)
    resource = resources / "groovia.gresource"
    resource.touch()
    scanner = resources / "libexec/gstreamer-1.0/gst-plugin-scanner"
    scanner.parent.mkdir(parents=True)
    scanner.touch()
    assert runtime.macos_bundle_contents(executable) == contents
    monkeypatch.setattr(runtime, "macos_bundle_contents", lambda executable=None: contents)
    assert runtime.bundled_resource_path("groovia.gresource") == resource
    assert runtime.find_gstreamer_scanner() == scanner


def test_mpris_is_disabled_for_macos(monkeypatch):
    configure_macos(monkeypatch)
    assert platform_compat.supports_mpris() is False
    assert platform_compat.media_backend_name() is None


def test_finder_reveal_uses_open_without_dbus(monkeypatch, tmp_path):
    configure_macos(monkeypatch)
    target = tmp_path / "Album With Spaces" / "Track 01.flac"
    target.parent.mkdir()
    target.touch()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(platform_compat.subprocess, "run", fake_run)
    monkeypatch.setitem(sys.modules, "gi", None)
    assert platform_compat.show_item_in_file_manager(target)
    assert calls == [(["open", "-R", str(target.resolve())], {"check": False})]


def test_bundled_tools_and_environment_support_spaces(monkeypatch, tmp_path):
    contents = tmp_path / "Groovia Preview.app/Contents"
    tools = contents / "Resources/tools"
    tools.mkdir(parents=True)
    for name in ("spotdl", "ffmpeg", "ffprobe", "deno"):
        (tools / name).touch()
    monkeypatch.setattr(runtime, "IS_MACOS", True)
    monkeypatch.setattr(runtime, "macos_bundle_contents", lambda executable=None: contents)
    assert runtime.bundled_tool_path("spotdl") == tools / "spotdl"
    environment = runtime.tool_process_environment({"PATH": "/usr/bin"})
    assert environment["PATH"].split(os.pathsep)[0] == str(tools)
    assert environment["FFMPEG_BINARY"] == str(tools / "ffmpeg")
    assert environment["FFPROBE_BINARY"] == str(tools / "ffprobe")
    assert environment["DENO_BINARY"] == str(tools / "deno")


def test_spotdl_private_posix_venv_resolution_with_spaces(monkeypatch, tmp_path):
    data = tmp_path / "Application Support"
    resolver = spotdl_module.SpotDLCommandResolver(data)
    executable = resolver.venv_dir / "bin/spotdl"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(spotdl_module, "IS_WINDOWS", False)
    monkeypatch.setattr(spotdl_module, "bundled_tool_path", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(spotdl_module.shutil, "which", lambda _name: None)
    assert (str(executable),) in resolver.candidates()


def test_development_gstreamer_path_preserves_system_search(monkeypatch, tmp_path):
    resources = tmp_path / "Resources With Spaces"
    plugins = resources / "gstreamer-1.0"
    plugins.mkdir(parents=True)
    monkeypatch.setattr(runtime, "is_standalone_bundle", lambda executable=None: False)
    monkeypatch.setenv("GST_PLUGIN_PATH_1_0", "/system/plugins")
    monkeypatch.setenv("GST_PLUGIN_SYSTEM_PATH_1_0", "/system/default-plugins")
    runtime._configure_bundle_environment(resources)
    assert os.environ["GST_PLUGIN_PATH_1_0"].split(os.pathsep) == [
        str(plugins),
        "/system/plugins",
    ]
    assert os.environ["GST_PLUGIN_SYSTEM_PATH_1_0"] == "/system/default-plugins"


def test_standalone_bundle_uses_only_embedded_runtime_paths(monkeypatch, tmp_path):
    resources = tmp_path / "Groovia.app/Contents/Resources"
    for directory in ("typelibs", "gstreamer-1.0", "share"):
        (resources / directory).mkdir(parents=True, exist_ok=True)
    scanner = resources / "libexec/gstreamer-1.0/gst-plugin-scanner"
    scanner.parent.mkdir(parents=True)
    scanner.touch()
    monkeypatch.setattr(runtime, "is_standalone_bundle", lambda executable=None: True)
    monkeypatch.setattr(runtime, "macos_bundle_contents", lambda executable=None: resources.parent)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("GI_TYPELIB_PATH", "/system/typelibs")
    monkeypatch.setenv("GST_PLUGIN_PATH_1_0", "/system/plugins")
    monkeypatch.setenv("GST_PLUGIN_SYSTEM_PATH_1_0", "/system/default-plugins")
    monkeypatch.setenv("XDG_DATA_DIRS", "/system/share")
    runtime._configure_bundle_environment(resources)
    assert os.environ["GI_TYPELIB_PATH"] == str(resources / "typelibs")
    assert os.environ["GST_PLUGIN_PATH_1_0"] == str(resources / "gstreamer-1.0")
    assert os.environ["GST_PLUGIN_SYSTEM_PATH_1_0"] == ""
    assert os.environ["XDG_DATA_DIRS"] == str(resources / "share")
    assert os.environ["GST_PLUGIN_SCANNER"] == str(scanner)


def test_missing_tempo_plugin_keeps_normal_audio_fallback():
    assert select_tempo_filter(lambda _name: None) is None
    available = {"scaletempo": object()}
    assert select_tempo_filter(available.get) == "scaletempo"
