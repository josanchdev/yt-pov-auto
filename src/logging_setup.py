"""structlog to stdout. JSON when not attached to a terminal."""

from __future__ import annotations

import logging
import os
import sys

import structlog

_configured = False


def configure(level: str | None = None) -> None:
    """Idempotent logging setup. Safe to call from every module entry point."""
    global _configured
    if _configured:
        return

    level_name = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level_name)

    interactive = sys.stdout.isatty()
    renderer = (
        structlog.dev.ConsoleRenderer() if interactive else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level_name]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    configure()
    return structlog.get_logger(name)
