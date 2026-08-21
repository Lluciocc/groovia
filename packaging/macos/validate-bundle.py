#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import tempfile
import time
from pathlib import Path

BREW_ROOTS = ("/opt/homebrew/", "/usr/local/Cellar/", "/usr/local/opt/")
SYSTEM_ROOTS = ("/System/Library/", "/usr/lib/")
REQUIRED_TYPELIBS = (
    "Adw-1.typelib",
    "Gdk-4.0.typelib",
    "GdkPixbuf-2.0.typelib",
    "Gio-2.0.typelib",
    "GLib-2.0.typelib",
    "GObject-2.0.typelib",
    "Graphene-1.0.typelib",
    "Gsk-4.0.typelib",
    "Gst-1.0.typelib",
    "GstAudio-1.0.typelib",
    "GstBase-1.0.typelib",
    "GstController-1.0.typelib",
    "GstNet-1.0.typelib",
    "GstPbutils-1.0.typelib",
    "GstTag-1.0.typelib",
    "GstVideo-1.0.typelib",
    "Gtk-4.0.typelib",
    "Pango-1.0.typelib",
    "PangoCairo-1.0.typelib",
    "cairo-1.0.typelib",
)


def run(
    *command: str,
    check: bool = True,
    env=None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def run_bytes(
    *command: str,
    check: bool = True,
    env=None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        env=env,
    )


def is_macho(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False

    try:
        return (
            b"Mach-O"
            in run_bytes(
                "file",
                "-b",
                str(path),
                check=False,
            ).stdout
        )
    except OSError:
        return False


def fail(message: str) -> None:
    raise SystemExit(f"[FAIL] {message}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an autonomous Groovia.app bundle")
    parser.add_argument("app", type=Path)
    parser.add_argument(
        "--architecture", choices=("arm64", "x86_64", "universal2"), default="arm64"
    )
    parser.add_argument(
        "--gui", action="store_true", help="also perform a WindowServer launch probe"
    )
    args = parser.parse_args()
    app = args.app.resolve()
    contents = app / "Contents"
    resources = contents / "Resources"
    frameworks = contents / "Frameworks"
    executable = contents / "MacOS" / "Groovia"
    for required in (contents / "Info.plist", executable, frameworks, resources):
        if not required.exists():
            fail(f"missing bundle component: {required}")

    with (contents / "Info.plist").open("rb") as stream:
        plist = plistlib.load(stream)
    expected = {
        "CFBundleName": "Groovia",
        "CFBundleIdentifier": "io.github.Lluciocc.Groovia",
        "CFBundleExecutable": "Groovia",
        "CFBundlePackageType": "APPL",
    }
    for key, value in expected.items():
        if plist.get(key) != value:
            fail(f"Info.plist {key}={plist.get(key)!r}, expected {value!r}")
    for relative in (
        "groovia.gresource",
        "VERSION",
        "Groovia.icns",
        "schemas/gschemas.compiled",
        "libexec/gstreamer-1.0/gst-plugin-scanner",
    ):
        if not (resources / relative).exists():
            fail(f"missing bundled resource: Contents/Resources/{relative}")
    for typelib in REQUIRED_TYPELIBS:
        if not (resources / "typelibs" / typelib).is_file():
            fail(f"missing bundled resource: Contents/Resources/typelibs/{typelib}")
    if not any((resources / "gstreamer-1.0").glob("*")):
        fail("bundled GStreamer plugin directory is empty")

    binaries = [path for path in app.rglob("*") if is_macho(path)]
    if not binaries:
        fail("bundle contains no Mach-O binaries")
    expected_arches = (
        {"arm64", "x86_64"} if args.architecture == "universal2" else {args.architecture}
    )
    for binary in binaries:
        arches = set(run("lipo", "-archs", str(binary)).stdout.split())
        if not expected_arches.issubset(arches):
            fail(
                f"{binary} architectures {sorted(arches)} do not contain {sorted(expected_arches)}"
            )
        lines = run("otool", "-L", str(binary)).stdout.splitlines()[1:]
        for line in lines:
            dependency = line.strip().split(" (compatibility", 1)[0]
            if dependency.startswith(BREW_ROOTS):
                fail(f"Homebrew dependency remains in {binary}: {dependency}")
            if dependency.startswith("/") and not dependency.startswith(SYSTEM_ROOTS):
                fail(f"unexpected external dependency in {binary}: {dependency}")
    print(f"[PASS] structure, plist, resources and {len(binaries)} Mach-O files")

    with tempfile.TemporaryDirectory(prefix="groovia-bundle-home-") as home:
        environment = dict(os.environ)
        environment.update(
            {
                "HOME": home,
                "GROOVIA_MUSIC_DIR": str(Path(home) / "Music"),
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent/groovia-dbus",
            }
        )
        result = run(
            str(executable), "--bundle-smoke-test", "--write-test", check=False, env=environment
        )
        if result.returncode != 0:
            fail(
                f"headless schema/audio/write smoke test failed:\n{result.stdout}\n{result.stderr}"
            )
        for relative in (
            "Library/Application Support/Groovia",
            "Library/Caches/Groovia",
            "Library/Preferences/Groovia",
        ):
            if not (Path(home) / relative).is_dir():
                fail(f"macOS data path was not written: {relative}")
        print("[PASS] GSettings, plugin discovery, generated audio playback and macOS writes")
        print(result.stdout.strip())

    if args.gui:
        process = subprocess.Popen(
            [str(executable)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        time.sleep(5)
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            fail(f"GUI process exited during launch probe:\n{stdout}\n{stderr}")
        process.terminate()
        process.wait(timeout=10)
        print("[PASS] GUI process remained alive for five seconds")
    else:
        print("[SKIP] graphical launch requires --gui and a logged-in WindowServer session")
    print("[PASS] bundle validation complete; Finder/MPRIS test used no session D-Bus")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
