"""Structured logging setup.

structlog in two flavors:
- dev: pretty colored consoled output, human-readable
- prod: JSON lines, one per event, ready for log aggregation

Every log call that happens within a request automatically carries
'request_id' thanks to the middleware binding contextvars.
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.types import Processor

from app.config import get_settings


def setup_logging() -> None:
    """Configure structlog + stdlib logging. Call once at app startup."""
    settings = get_settings()

    # stdlib logging: send everything ro stdput at the configured level
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=settings.log_level,
    )

    # Sharedprocessors run for every log call, dev or prod
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,    # injects request_id etc.
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.is_prod:
        # JSON lines for prod - one log event = one parseable JSON object
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        # Pretty colored output for local dev
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.log_level]
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a logger. Use this everywhere instead of logging.getLogger()."""
    return structlog.get_logger(name)
