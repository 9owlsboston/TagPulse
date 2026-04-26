"""Structured JSON logging configuration."""

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Format log records as JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            if isinstance(record.exc_info, tuple) and record.exc_info[1] is not None:
                log_entry["exception"] = self.formatException(record.exc_info)
            elif record.exc_text:
                log_entry["exception"] = record.exc_text
        if hasattr(record, "request_id"):
            log_entry["request_id"] = record.__dict__["request_id"]
        return json.dumps(log_entry)


def setup_logging(level: str = "info") -> None:
    """Configure root logger with JSON formatter on stdout."""
    root = logging.getLogger()
    root.setLevel(level.upper())

    # Remove existing handlers to avoid duplicates on reload
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)

    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
