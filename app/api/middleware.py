"""FastAPI middleware - request_id generation + logging context.

Every incoming request gets a UUID bound to structlog's contextvars.
Every log call wihtin that request's scope automatically includes request_id,
method, path, and duration_ms.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response

from app.observability.logger import get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

async def request_context_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Bind request_id to structlog context, log request start/end with duration."""
    # Respect inbound header if present (useful for distributed tracing later)
    request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        method=request.method,
        path=request.url.path,
    )

    start = time.perf_counter()
    logger.info("request.started")

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.exception("request.failed", duration_ms=round(duration_ms, 2))
        raise

    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "request.completed",
        status_code=response.status_code,
        duration_ms=round(duration_ms, 2),
    )

    # Echo request_id back so clients can correlate
    response.headers[REQUEST_ID_HEADER] = request_id
    return response
