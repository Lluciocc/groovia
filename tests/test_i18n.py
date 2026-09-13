from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.i18n import DOMAIN, _, ngettext
from src.runtime import _configure_translations, _ensure_language_environment

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def compiled_locale(tmp_path):
    localedir = tmp_path / "locale"
    catalog = localedir / "fr" / "LC_MESSAGES" / f"{DOMAIN}.mo"
    catalog.parent.mkdir(parents=True)
    subprocess.run(
        ["msgfmt", "--check", "--check-format", "-o", catalog, ROOT / "po" / "fr.po"],
        check=True,
    )
    return localedir


def test_gettext_domain():
    assert DOMAIN == "groovia"


def test_french_translation_plural_and_placeholder(monkeypatch, compiled_locale):
    monkeypatch.setenv("LANGUAGE", "fr")
    _configure_translations(compiled_locale)

    assert _("Library") == "Bibliothèque"
    assert ngettext("%(count)d track", "%(count)d tracks", 1) % {"count": 1} == "1 morceau"
    assert ngettext("%(count)d track", "%(count)d tracks", 3) % {"count": 3} == "3 morceaux"
    assert (
        _("Play %(title)s by %(artist)s")
        % {
            "title": "Titre",
            "artist": "Artiste",
        }
        == "Lire Titre par Artiste"
    )


def test_reinitialization_keeps_configured_translation_directory(monkeypatch, compiled_locale):
    monkeypatch.setenv("LANGUAGE", "fr")
    _configure_translations(compiled_locale)

    selected = _configure_translations()

    assert selected == compiled_locale
    assert _("Library") == "Bibliothèque"


@pytest.mark.parametrize("language", ["en", "zz_UNKNOWN"])
def test_english_fallback(monkeypatch, compiled_locale, language):
    monkeypatch.setenv("LANGUAGE", language)
    _configure_translations(compiled_locale)
    assert _("Library") == "Library"


def test_missing_locale_directory_does_not_fail(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGUAGE", "fr")
    missing = tmp_path / "missing"
    assert _configure_translations(missing) == missing
    assert _("Library") == "Library"


def test_system_locale_is_used_when_language_variables_are_missing(monkeypatch):
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("src.runtime.locale.getlocale", lambda _category: ("fr_FR", "UTF-8"))

    _ensure_language_environment()

    assert os.environ["LANGUAGE"] == "fr_FR"


def test_installed_locale_layout(monkeypatch, compiled_locale, tmp_path):
    monkeypatch.setenv("LANGUAGE", "fr")
    installed = tmp_path / "prefix" / "share" / "locale"
    target = installed / "fr" / "LC_MESSAGES"
    target.mkdir(parents=True)
    shutil.copy2(compiled_locale / "fr" / "LC_MESSAGES" / f"{DOMAIN}.mo", target)

    assert _configure_translations(installed) == installed
    assert _("Library") == "Bibliothèque"


def test_frozen_bundle_locale_layout(monkeypatch, compiled_locale, tmp_path):
    monkeypatch.setenv("LANGUAGE", "fr")
    bundle = tmp_path / "bundle"
    target = bundle / "locale" / "fr" / "LC_MESSAGES"
    target.mkdir(parents=True)
    shutil.copy2(compiled_locale / "fr" / "LC_MESSAGES" / f"{DOMAIN}.mo", target)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    assert _configure_translations(resource_dir=bundle) == bundle / "locale"
    assert _("Library") == "Bibliothèque"


def test_po_catalog_is_valid():
    subprocess.run(
        ["msgfmt", "--check", "--check-format", ROOT / "po" / "fr.po"],
        check=True,
    )


def test_potfiles_entries_exist_and_cover_user_interface():
    entries = {
        line.strip()
        for line in (ROOT / "po" / "POTFILES.in").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert all((ROOT / entry).is_file() for entry in entries)
    assert {
        "src/main.py",
        "src/preferences.py",
        "src/window.py",
        "src/audio/player.py",
        "src/downloads/importer.py",
        "src/downloads/manager.py",
        "src/downloads/service.py",
        "src/downloads/spotdl.py",
        "src/widgets/lyrics_view.py",
        "src/shortcuts-dialog.ui",
        "data/io.github.Lluciocc.Groovia.desktop.in",
        "data/io.github.Lluciocc.Groovia.metainfo.xml.in",
        "data/io.github.Lluciocc.Groovia.gschema.xml",
    } <= entries
    extracted_python = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "src").rglob("*.py")
        if path.name != "i18n.py"
        and re.search(
            r"(?<![\w.])(?:_|ngettext|pgettext)\(",
            path.read_text(encoding="utf-8"),
        )
    }
    assert extracted_python <= entries


def test_builder_uses_project_translation_domain():
    main_source = (ROOT / "src" / "main.py").read_text(encoding="utf-8")
    assert 'set_translation_domain("groovia")' in main_source
