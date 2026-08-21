#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
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
    return source.resolve()


def validate_layout(framework: Path) -> None:
    versions = framework / "Versions"
    current = versions / "Current"
    if not versions.is_dir() or not current.is_symlink():
        raise SystemExit(f"Invalid Python.framework layout: {framework}")
    version = current.resolve()
    if version.parent != versions or not version.is_dir():
        raise SystemExit(f"Invalid Python.framework version link: {current}")
    for name in ("Python", "Resources"):
        alias = framework / name
        expected = Path("Versions") / "Current" / name
        if not alias.is_symlink() or Path(alias.readlink()) != expected:
            raise SystemExit(f"Invalid Python.framework alias: {alias}")
    if not (version / "Python").is_file() or not (version / "Resources").is_dir():
        raise SystemExit(f"Incomplete Python.framework version: {version}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a symlink-preserving Python.framework")
    parser.add_argument("app", type=Path)
    args = parser.parse_args()

    destination = args.app.resolve() / "Contents" / "Frameworks" / "Python.framework"
    source = framework_source()
    validate_layout(source)
    if destination.is_symlink():
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=True)
    validate_layout(destination)
    print(f"Restored Python.framework {destination} from {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
