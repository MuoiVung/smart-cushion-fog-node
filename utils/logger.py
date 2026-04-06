"""
Logging utilities for the Smart Cushion Fog Node.

Provides structured, colourised console logging with an optional
file handler. Call setup_logging() once at application startup.
"""

import logging
import logging.handlers
import sys
from pathlib import Path

# ANSI colour codes for terminal output
_COLOURS = {
    logging.DEBUG:    "\033[36m",   # Cyan
    logging.INFO:     "\033[32m",   # Green
    logging.WARNING:  "\033[33m",   # Yellow
    logging.ERROR:    "\033[31m",   # Red
    logging.CRITICAL: "\033[35m",   # Magenta
}
_RESET = "\033[0m"


class ColouredFormatter(logging.Formatter):
    """Logging formatter that adds ANSI colour codes to log levels."""

    _FMT = "%(asctime)s [%(levelname)s] %(name)s – %(message)s"
    _DATE_FMT = "%Y-%m-%d %H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:
        colour = _COLOURS.get(record.levelno, "")
        record.levelname = f"{colour}{record.levelname:<8}{_RESET}"
        formatter = logging.Formatter(self._FMT, datefmt=self._DATE_FMT)
        return formatter.format(record)


class PlainFormatter(logging.Formatter):
    """Plain formatter without ANSI codes – used for file logging."""

    _FMT = "%(asctime)s [%(levelname)s] %(name)s – %(message)s"
    _DATE_FMT = "%Y-%m-%d %H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:
        formatter = logging.Formatter(self._FMT, datefmt=self._DATE_FMT)
        return formatter.format(record)


def setup_logging(
    level: int = logging.INFO,
    log_dir: str = "logs",
    enable_file_log: bool = True,
) -> None:
    """
    Initialise application-wide logging.

    Args:
        level:           Root log level (e.g. logging.DEBUG for verbose output).
        log_dir:         Directory to write rotating log files.
        enable_file_log: Write logs to a file in addition to the console.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers (prevent duplicate messages on re-init)
    root.handlers.clear()

    # ── Console handler (colourised) ────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ColouredFormatter())
    root.addHandler(console_handler)

    # ── File handler (rotating, plain text) ─────────────────────────────────
    if enable_file_log:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path / "fog_node.log",
            maxBytes=5 * 1024 * 1024,  # 5 MB per file
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(PlainFormatter())
        root.addHandler(file_handler)

    # Silence noisy third-party libraries
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("paho").setLevel(logging.WARNING)
