"""Structured JSON logging.

Every log line is a single JSON object, so logs are machine-queryable in
any aggregator (CloudWatch, Loki, Datadog). A contextvar-based correlation
id ties together every line produced while analyzing one project — set once
by the pipeline runner, present in all downstream logs automatically.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def set_correlation_id(value: str | None) -> None:
    """Bind a correlation id (e.g. an analysis job id) to the current context."""
    _correlation_id.set(value)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        correlation_id = get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging exactly once with the JSON formatter."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_with_fields(logger: logging.Logger, level: int, message: str, **fields) -> None:
    """Log a message with arbitrary structured fields attached."""
    logger.log(level, message, extra={"extra_fields": fields})
