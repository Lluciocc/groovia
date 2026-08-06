"""PyInstaller entry point for the Groovia package."""

import sys

from groovia.runtime import initialize_runtime


def _startup_error(error: Exception) -> None:
    message = str(error)
    if "typelib" in message.lower():
        detail = "A required GObject typelib is missing from the Groovia bundle."
    else:
        detail = "The GTK/Libadwaita runtime is missing or could not be loaded."
    text = f"Groovia could not start.\n\n{detail}\n\n{message}"
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text, "Groovia", 0x10)
    except Exception:
        print(text, file=sys.stderr)


if __name__ == "__main__":
    try:
        initialize_runtime()
        from groovia import main
    except (ImportError, OSError) as error:
        _startup_error(error)
        sys.exit(1)

    sys.exit(main.main("0.1.0"))
