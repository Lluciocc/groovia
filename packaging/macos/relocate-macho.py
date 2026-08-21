#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

BREW_ROOTS = ("/opt/homebrew/", "/usr/local/Cellar/", "/usr/local/opt/")
SYSTEM_ROOTS = ("/System/Library/", "/usr/lib/")


def output(*command: str) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def is_macho(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        return "Mach-O" in output("file", "-b", str(path))
    except (OSError, subprocess.CalledProcessError):
        return False


def dependencies(path: Path) -> list[str]:
    lines = output("otool", "-L", str(path)).splitlines()[1:]
    values = [line.strip().split(" (compatibility", 1)[0] for line in lines if line.strip()]
    identity = output("otool", "-D", str(path)).splitlines()[1:]
    dylib_id = identity[0].strip() if identity else None
    return [value for value in values if value != dylib_id]


def main() -> int:
    parser = argparse.ArgumentParser(description="Relocate Homebrew Mach-O dependencies")
    parser.add_argument("app", type=Path)
    args = parser.parse_args()
    app = args.app.resolve()
    frameworks = app / "Contents" / "Frameworks"
    if app.suffix != ".app" or not frameworks.is_dir():
        parser.error(f"invalid application bundle: {app}")

    changed = True
    while changed:
        changed = False
        binaries = [path for path in app.rglob("*") if is_macho(path)]
        known = {path.name: path for path in binaries}
        for binary in binaries:
            for dependency in dependencies(binary):
                if not dependency.startswith(BREW_ROOTS):
                    continue
                source = Path(dependency).resolve()
                bundled = known.get(source.name)
                if bundled is None:
                    if not source.is_file():
                        raise SystemExit(f"Missing Homebrew dependency: {dependency}")
                    bundled = frameworks / source.name
                    if bundled.exists() and bundled.resolve() != source:
                        raise SystemExit(f"Mach-O basename collision: {source.name}")
                    shutil.copy2(source, bundled)
                    known[source.name] = bundled
                    changed = True
                subprocess.run(
                    [
                        "install_name_tool",
                        "-change",
                        dependency,
                        f"@rpath/{source.name}",
                        str(binary),
                    ],
                    check=True,
                )

    binaries = [path for path in app.rglob("*") if is_macho(path)]
    for binary in binaries:
        if binary.suffix in {".dylib", ".so"}:
            subprocess.run(
                ["install_name_tool", "-id", f"@rpath/{binary.name}", str(binary)],
                check=False,
                capture_output=True,
            )
        subprocess.run(
            ["install_name_tool", "-add_rpath", "@executable_path/../Frameworks", str(binary)],
            check=False,
            capture_output=True,
        )

    forbidden = []
    for binary in binaries:
        for dependency in dependencies(binary):
            if dependency.startswith(BREW_ROOTS):
                forbidden.append(f"{binary}: {dependency}")
            elif dependency.startswith("/") and not dependency.startswith(SYSTEM_ROOTS):
                forbidden.append(f"{binary}: unexpected absolute dependency {dependency}")
    if forbidden:
        raise SystemExit("Unrelocated Mach-O dependencies:\n" + "\n".join(forbidden))
    print(f"Relocated {len(binaries)} Mach-O files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
