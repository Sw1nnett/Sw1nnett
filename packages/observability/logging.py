"""Structured logging via structlog."""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
from typing import Any

import structlog


_SECRET_RE = re.compile(
    r"(api[_-]?key|secret|password|token|bearer|authorization|credential)",
    re.IGNORECASE,
)


def _censor_secrets(
    logger: Any, method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor that redacts secret-looking values."""
    for key in list(event_dict.keys()):
        if _SECRET_RE.search(str(key)):
            event_dict[key] = "***"
    return event_dict


def configure_logging(
    level: str = "INFO",
    log_dir: str = "logs",
    json_console: bool = False,
) -> None:
    """Configure structlog + standard library logging."""
    os.makedirs(log_dir, exist_ok=True)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _censor_secrets,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Any
    if json_console:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    console_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # File handler (JSON)
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "app.jsonl"),
        maxBytes=50 * 1024 * 1024,
        backupCount=5,
    )
    fh.setFormatter(formatter)
    root.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(console_formatter)
    root.addHandler(ch)


class AuditLogger:
    """Writes immutable audit events to a separate rotating file."""

    def __init__(self, log_dir: str = "logs") -> None:
        os.makedirs(log_dir, exist_ok=True)
        self._handler = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "audit.jsonl"),
            maxBytes=100 * 1024 * 1024,
            backupCount=10,
        )
        self._handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.JSONRenderer(),
                ],
                foreign_pre_chain=[
                    structlog.processors.TimeStamper(fmt="iso"),
                    _censor_secrets,
                ],
            )
        )
        self._logger = logging.getLogger("audit")
        self._logger.addHandler(self._handler)
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

    def log(self, action: str, **kwargs: Any) -> None:
        self._logger.info(action, extra={"_extra": kwargs})


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def get_audit_logger(log_dir: str = "logs") -> AuditLogger:
    return AuditLogger(log_dir=log_dir)
