"""Logging configuration for ANIMA.

Uses TimedRotatingFileHandler — keeps only last 24 hours of logs.
All handlers use UTF-8 encoding to prevent GBK emoji crashes on Windows.
"""

import io
import json as _json
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter with correlation_id support."""

    def format(self, record: logging.LogRecord) -> str:
        from anima.utils.log_context import get_correlation_id
        entry = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        cid = get_correlation_id()
        if cid:
            entry["cid"] = cid
        if record.exc_info and record.exc_info[0]:
            entry["exc"] = self.formatException(record.exc_info)
        return _json.dumps(entry, ensure_ascii=False)


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
    console: bool = True,
    json_file: bool = False,
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
        file_fmt = JSONFormatter(datefmt="%Y-%m-%d %H:%M:%S") if json_file else fmt
        fh.setFormatter(file_fmt)
        logger.addHandler(fh)

    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"anima.{name}")
