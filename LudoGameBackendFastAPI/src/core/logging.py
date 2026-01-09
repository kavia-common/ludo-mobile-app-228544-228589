from __future__ import annotations

import logging
from typing import Optional


class RequestIdFilter(logging.Filter):
    """Attach request_id from LogRecord extras if present; otherwise set '-'."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return True


# PUBLIC_INTERFACE
def configure_logging(level: str = "INFO") -> None:
    """Configure application logging with a consistent format and request-id support."""
    root = logging.getLogger()
    root.setLevel(level.upper())

    # Avoid duplicate handlers if reloads occur.
    if root.handlers:
        return

    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] request_id=%(request_id)s %(message)s"
    )
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())
    root.addHandler(handler)


# PUBLIC_INTERFACE
def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(name)
