#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
import sys
import sysconfig
from pathlib import Path


def framework_source() -> Path:
    framework = sysconfig.get_config_var("PYTHONFRAMEWORK")
    prefix = sysconfig.get_config_var("PYTHONFRAMEWORKPREFIX")
    if not framework or not prefix:
        raise SystemExit("The build interpreter is not using a Python.framework")
    source = Path(prefix) / f"{framework}.framework"
    if not source.is_dir():
        raise SystemExit(f"Python.framework not found at {source}")
    return source


def framework_version(framework: Path) -> str:
    versions = framework / "Versions"
    current = versions / "Current"
    if not versions.is_dir():
        raise SystemExit(f"Python.framework has no Versions directory: {framework}")
    version_dirs = [path for path in versions.iterdir() if path.is_dir() and path.name != "Current"]
    if len(version_dirs) != 1:
        raise SystemExit(
            f"Expected one Python.framework version in {versions}, found "
            f"{[path.name for path in version_dirs]}"
        )
    if not current.is_symlink():
        raise SystemExit(f"Python.framework Versions/Current is not a symlink: {current}")
    target = current.resolve()
    if target != version_dirs[0].resolve():
        raise SystemExit(f"Python.framework Current does not select {version_dirs[0].name}")
    return version_dirs[0].name


def validate_layout(framework: Path, expected_version: str) -> None:
    version_name = framework_version(framework)
    if version_name != expected_version:
        raise SystemExit(
            f"Python.framework version {version_name} does not match build Python "
            f"{expected_version}: {framework}"
        )
    version = framework / "Versions" / version_name
    for name in ("Python", "Resources"):
        alias = framework / name
        if not alias.is_symlink():
            raise SystemExit(f"Python.framework/{name} is not a symlink: {alias}")
    if not (version / "Python").is_file() or not (version / "Resources").is_dir():
        raise SystemExit(f"Incomplete Python.framework version: {version}")


def normalize_aliases(framework: Path) -> None:
    for name in ("Python", "Resources"):
        alias = framework / name
        if not alias.is_symlink():
            raise SystemExit(f"Python.framework/{name} is not a symlink: {alias}")
        target = Path(alias.readlink())
        expected = Path("Versions") / "Current" / name
        direct = Path("Versions") / framework_version(framework) / name
        if target not in {expected, direct}:
            raise SystemExit(f"Unexpected Python.framework alias target: {alias} -> {target}")
        if target != expected:
            alias.unlink()
            alias.symlink_to(expected)


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a symlink-preserving Python.framework")
    parser.add_argument("app", type=Path)
    args = parser.parse_args()

    destination = args.app.resolve() / "Contents" / "Frameworks" / "Python.framework"
    source = framework_source()
    expected_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    print(f"Build Python {sys.version.split()[0]} framework source: {source}")
    validate_layout(source, expected_version)
    if destination.is_symlink():
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=True)
    normalize_aliases(destination)
    validate_layout(destination, expected_version)
    print(f"Restored Python.framework {destination} from {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
