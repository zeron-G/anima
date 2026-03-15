"""Logging configuration for ANIMA."""

import logging
import sys
from pathlib import Path


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
    console: bool = True,
) -> logging.Logger:
    """Configure and return the ANIMA logger.

    Args:
        level: Log level.
        log_file: Path to log file (always written if provided).
        console: If False, suppress console output (for interactive mode).
    """
    logger = logging.getLogger("anima")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear existing handlers on reconfigure
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Console handler — only if requested
    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    # File handler — always add if path provided
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(path), encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the anima namespace."""
    return logging.getLogger(f"anima.{name}")
