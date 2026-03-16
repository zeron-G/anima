"""Logging configuration for ANIMA.

Uses TimedRotatingFileHandler — keeps only last 24 hours of logs.
All handlers use UTF-8 encoding to prevent GBK emoji crashes on Windows.
"""

import io
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
        # Force UTF-8 on the console stream — multiple fallback layers
        stream = sys.stderr
        if stream:
            if hasattr(stream, "reconfigure"):
                try:
                    stream.reconfigure(encoding="utf-8", errors="replace")
                except Exception:
                    pass

            # If stream.encoding is still GBK/cp936, wrap it
            enc = getattr(stream, "encoding", "").lower()
            if enc and enc not in ("utf-8", "utf8"):
                try:
                    stream = io.TextIOWrapper(
                        stream.buffer, encoding="utf-8", errors="replace",
                        line_buffering=True,
                    )
                except Exception:
                    pass  # pythonw.exe may not have .buffer

        ch = logging.StreamHandler(stream)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
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
