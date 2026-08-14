"""Console logging helpers shared by the Linux and Windows builds."""

import ctypes
import logging
import os
import sys

_RESET = "\033[0m"
_LEVEL_COLORS = {
    logging.DEBUG: "\033[90m",
    logging.INFO: "\033[36m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}


# This is a workaround for Windows, where the console does not support
# ANSI escape codes by default.
# The function attempts to enable ANSI support for the console if running on Windows.
# found somewhere on the internet
def _enable_windows_ansi(stream) -> bool:
    if os.name != "nt":
        return True
    try:
        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not handle or not ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


class ConsoleFormatter(logging.Formatter):
    def __init__(self, label: str, use_color: bool):
        super().__init__(f"[{label}] %(message)s")
        self.label = label
        self.use_color = use_color

    def format(self, record):
        message = super().format(record)
        if not self.use_color:
            return message
        color = _LEVEL_COLORS.get(record.levelno, "")
        return f"{color}{message}{_RESET}" if color else message


def configure_logger(logger: logging.Logger, label: str) -> None:
    if logger.handlers:
        return
    stream = sys.stderr
    use_color = bool(
        not os.environ.get("NO_COLOR")
        and hasattr(stream, "isatty")
        and stream.isatty()
        and _enable_windows_ansi(stream)
    )
    handler = logging.StreamHandler(stream)
    handler.setFormatter(ConsoleFormatter(label, use_color))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
