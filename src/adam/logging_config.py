"""Structured logging configuration for Adam.

Sets up console + file logging with Rich handler for pretty terminal output.
"""

from __future__ import annotations

import logging
from pathlib import Path

from rich.logging import RichHandler


def setup_logging(
    level: str = "INFO",
    log_file: Path | None = None,
    debug: bool = False,
) -> None:
    """Configure logging for Adam.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR).
        log_file: Optional file path for log output.
        debug: If True, sets level to DEBUG and enables verbose output.
    """
    if debug:
        level = "DEBUG"

    log_level = getattr(logging, level.upper(), logging.INFO)

    # Root logger — set to DEBUG if file logging is enabled
    # (file handler captures everything, console handler filters by level)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if log_file else log_level)

    # Clear existing handlers
    root.handlers.clear()

    # Rich console handler — pretty output for terminal
    console_handler = RichHandler(
        level=log_level,
        show_time=True,
        show_path=debug,
        markup=True,
        rich_tracebacks=True,
        tracebacks_show_locals=debug,
    )
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console_handler)

    # File handler — detailed logs for debugging
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(name)s %(levelname)-8s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root.addHandler(file_handler)

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if debug else logging.WARNING
    )
    logging.getLogger("asyncio").setLevel(logging.WARNING)
