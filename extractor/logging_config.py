"""Structured JSON logging with request-scoped IDs.

One JSON object per line on stderr. Schema:
    {"ts":"2026-05-01T12:34:56.789Z","level":"INFO",
     "logger":"extractor.pipeline","event":"pipeline.done",
     "request_id":"a1b2c3d4", ...custom fields}

Pipeline modules call ``log_event(LOG, "pipeline.done", duration_ms=123)``.
The ``request_id`` is a ``ContextVar`` set by the FastAPI middleware once
per request, so deeper code (fetcher, locator, ...) inherits it without
threading it through every signature.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any


_request_id_var: ContextVar[str | None] = ContextVar(
    "sec_extractor_request_id", default=None
)


def get_request_id() -> str | None:
    return _request_id_var.get()


def set_request_id(rid: str | None):
    return _request_id_var.set(rid)


def reset_request_id(token) -> None:
    _request_id_var.reset(token)


def new_request_id() -> str:
    return uuid.uuid4().hex[:8]


# Standard LogRecord attributes we must NOT clobber when merging custom
# fields into the JSON envelope. Anything else in record.__dict__ that
# came from `extra={...}` is fair game.
_RESERVED_LOGRECORD_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "taskName", "thread", "threadName",
})


class JsonFormatter(logging.Formatter):
    """Serialize each LogRecord as one compact JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        secs = int(record.created)
        ms = int((record.created - secs) * 1000)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(secs))
        out: dict[str, Any] = {
            "ts": f"{ts}.{ms:03d}Z",
            "level": record.levelname,
            "logger": record.name,
        }
        event = getattr(record, "event", None)
        if event is None:
            event = record.getMessage()
        out["event"] = event

        rid = _request_id_var.get()
        if rid:
            out["request_id"] = rid

        # Merge any extra={"foo": "bar"} fields the caller passed.
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOGRECORD_ATTRS or key in out or key == "event":
                continue
            out[key] = value

        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)

        return json.dumps(out, default=str, ensure_ascii=False)


_configured = False


def setup_logging(level: int | str = "INFO") -> None:
    """Install the JSON handler on the root logger. Idempotent.

    Safe to call from multiple entry points (server startup, CLI,
    individual tests). Subsequent calls only update the level.
    """
    global _configured
    root = logging.getLogger()
    if isinstance(level, str):
        level = logging.getLevelName(level)
    if _configured:
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    event: str,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit one structured event. Skips work if the level is disabled."""
    if not logger.isEnabledFor(level):
        return
    extra = dict(fields)
    extra["event"] = event
    logger.log(level, event, extra=extra)
