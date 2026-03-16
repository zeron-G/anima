"""Logging configuration for ANIMA.

Uses TimedRotatingFileHandler — keeps only last 24 hours of logs.
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
    console: bool = True,
) -> logging.Logger:
    logger = logging.getLogger("anima")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if console:
        stream = sys.stderr
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        ch = logging.StreamHandler(stream)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Rotate at midnight, keep 1 backup (= 24h of logs)
        fh = TimedRotatingFileHandler(
            str(path),
            when="midnight",
            backupCount=1,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"anima.{name}")
