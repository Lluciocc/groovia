#!/usr/bin/env python3
"""Refresh the pinned Flatpak wheels and Windows tool downloads."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
FLATPAK_MANIFEST = ROOT / "io.github.Lluciocc.Groovia.json"
WINDOWS_MANIFEST = ROOT / "packaging" / "windows" / "dependencies.json"
PYTHON_TAG = "cp313"
ARCHES = {"x86_64": "x86_64", "aarch64": "aarch64"}
USER_AGENT = "Groovia dependency updater"


def get_json(url: str) -> dict | list:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if url.startswith("https://api.github.com/") and (token := os.environ.get("GITHUB_TOKEN")):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def download_sha256(url: str) -> str:
    headers = {"User-Agent": USER_AGENT}
    request = urllib.request.Request(url, headers=headers)
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=120) as response:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def stable_versions(project: dict) -> list[Version]:
    versions = []
    for raw_version, files in project["releases"].items():
        version = Version(raw_version)
        if (
            files
            and not version.is_prerelease
            and any(not item.get("yanked", False) for item in files)
        ):
            versions.append(version)
    return sorted(versions, reverse=True)


def wheels_for_release(project: dict, version: Version) -> dict[str, dict] | None:
    selected = {}
    for arch, filename_arch in ARCHES.items():
        matches = [
            item
            for item in project["releases"][str(version)]
            if item["packagetype"] == "bdist_wheel"
            and f"-{PYTHON_TAG}-{PYTHON_TAG}-" in item["filename"]
            and "manylinux" in item["filename"]
            and "musllinux" not in item["filename"]
            and item["filename"].endswith(f"_{filename_arch}.whl")
            and not item.get("yanked", False)
        ]
        if len(matches) != 1:
            return None
        selected[arch] = matches[0]
    return selected


def latest_release_with_wheels(name: str, constraint: SpecifierSet | None = None):
    project = get_json(f"https://pypi.org/pypi/{name}/json")
    for version in stable_versions(project):
        if constraint is not None and version not in constraint:
            continue
        wheels = wheels_for_release(project, version)
        if wheels:
            metadata = get_json(f"https://pypi.org/pypi/{name}/{version}/json")
            return version, wheels, metadata
    raise RuntimeError(f"No {PYTHON_TAG} manylinux wheels found for {name}")


def scipy_numpy_constraint(metadata: dict) -> SpecifierSet:
    environment = default_environment()
    environment.update(
        python_version="3.13",
        python_full_version="3.13.0",
        platform_machine="x86_64",
        sys_platform="linux",
    )
    constraints = []
    for raw_requirement in metadata["info"].get("requires_dist") or []:
        requirement = Requirement(raw_requirement)
        if requirement.name.lower() != "numpy":
            continue
        if requirement.marker is None or requirement.marker.evaluate(environment):
            constraints.append(str(requirement.specifier))
    return SpecifierSet(",".join(filter(None, constraints)))


def update_flatpak() -> None:
    scipy_version, scipy_wheels, scipy_metadata = latest_release_with_wheels("scipy")
    numpy_version, numpy_wheels, _ = latest_release_with_wheels(
        "numpy", scipy_numpy_constraint(scipy_metadata)
    )

    manifest = json.loads(FLATPAK_MANIFEST.read_text(encoding="utf-8"))
    module = next(item for item in manifest["modules"] if item["name"] == "groovia-autodj")
    module["build-commands"] = [
        "python3 -m pip install --no-index --no-cache-dir --no-deps "
        f"--prefix=/app --find-links=. numpy=={numpy_version} scipy=={scipy_version}"
    ]
    sources = []
    for name, wheels in (("numpy", numpy_wheels), ("scipy", scipy_wheels)):
        for arch in ARCHES:
            wheel = wheels[arch]
            calculated_hash = pinned_download(wheel["url"]).lower()
            if calculated_hash != wheel["digests"]["sha256"].lower():
                raise RuntimeError(f"PyPI checksum mismatch for {wheel['filename']}")
            sources.append(
                {
                    "type": "file",
                    "url": wheel["url"],
                    "sha256": calculated_hash,
                    "only-arches": [arch],
                }
            )
    module["sources"] = sources
    FLATPAK_MANIFEST.write_text(json.dumps(manifest, indent=4) + "\n", encoding="utf-8")
    print(f"Flatpak: NumPy {numpy_version}, SciPy {scipy_version}")


def github_latest(repo: str) -> dict:
    return get_json(f"https://api.github.com/repos/{repo}/releases/latest")


def asset(release: dict, exact_name: str) -> dict:
    matches = [item for item in release["assets"] if item["name"] == exact_name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one release asset named {exact_name!r}")
    return matches[0]


def latest_ffmpeg() -> tuple[dict, dict]:
    releases = get_json("https://api.github.com/repos/BtbN/FFmpeg-Builds/releases?per_page=10")
    release = next(item for item in releases if item["tag_name"].startswith("autobuild-"))
    matches = [
        item
        for item in release["assets"]
        if re.fullmatch(r"ffmpeg-N-.+-win64-gpl\.zip", item["name"])
    ]
    if len(matches) != 1:
        raise RuntimeError("Expected one pinned win64 GPL FFmpeg archive")
    return release, matches[0]


def pinned_download(url: str) -> str:
    # Downloading before writing the manifest proves that the URL exists and
    # makes the checksum belong to the exact artifact that will be committed.
    return download_sha256(url).upper()


def update_windows() -> None:
    manifest = json.loads(WINDOWS_MANIFEST.read_text(encoding="utf-8"))

    spotdl_release = github_latest("spotDL/spotify-downloader")
    spotdl_version = spotdl_release["tag_name"].removeprefix("v")
    spotdl_name = f"spotdl-{spotdl_version}-win32.exe"
    spotdl_asset = asset(spotdl_release, spotdl_name)
    spotdl_license = (
        f"https://raw.githubusercontent.com/spotDL/spotify-downloader/"
        f"{spotdl_release['tag_name']}/LICENSE"
    )
    manifest["spotdl"].update(
        version=spotdl_version,
        url=spotdl_asset["browser_download_url"],
        sha256=pinned_download(spotdl_asset["browser_download_url"]),
        cache_name=spotdl_name,
        license_url=spotdl_license,
        license_sha256=pinned_download(spotdl_license),
    )

    deno_release = github_latest("denoland/deno")
    deno_version = deno_release["tag_name"].removeprefix("v")
    deno_name = "deno-x86_64-pc-windows-msvc.zip"
    deno_asset = asset(deno_release, deno_name)
    deno_license = (
        f"https://raw.githubusercontent.com/denoland/deno/{deno_release['tag_name']}/LICENSE.md"
    )
    manifest["deno"].update(
        version=deno_version,
        url=deno_asset["browser_download_url"],
        sha256=pinned_download(deno_asset["browser_download_url"]),
        cache_name=deno_name,
        license_url=deno_license,
        license_sha256=pinned_download(deno_license),
    )

    ffmpeg_release, ffmpeg_asset = latest_ffmpeg()
    ffmpeg_build = ffmpeg_asset["name"].removeprefix("ffmpeg-").removesuffix("-win64-gpl.zip")
    manifest["ffmpeg"].update(
        version=f"{ffmpeg_release['tag_name']} / {ffmpeg_build}",
        url=ffmpeg_asset["browser_download_url"],
        sha256=pinned_download(ffmpeg_asset["browser_download_url"]),
        cache_name=ffmpeg_asset["name"],
    )

    WINDOWS_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Windows: spotDL {spotdl_version}, Deno {deno_version}, FFmpeg {ffmpeg_build}")


def main() -> None:
    update_flatpak()
    update_windows()


if __name__ == "__main__":
    main()
