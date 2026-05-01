"""FastAPI application entry point."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app import __version__
from app.api.middleware import request_context_middleware
from app.api.routes import router as api_router
from app.config import get_settings
from app.observability.logger import get_logger, setup_logging
from app.observability.metrics import MetricsCollector
from app.tools import build_default_registry


def create_app() -> FastAPI:
    """Application factory. Keeps main importable without side effects."""
    setup_logging()
    settings = get_settings()
    logger = get_logger(__name__)

    app = FastAPI(
        title="0xpilot",
        description="Autonomous Web3 research agent with tool use",
        version=__version__,
    )

    # Tool registry — built once at startup, shared across all requests
    app.state.tool_registry = build_default_registry()

    # Metrics collector - single instance for the lifetime of the app
    app.state.metrics = MetricsCollector()

    # Middleware — CORS outermost, then request context (wraps actual handler)
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.add_middleware(BaseHTTPMiddleware, dispatch=request_context_middleware)

    # Routes
    app.include_router(api_router)

    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
    if frontend_dir.exists():
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        app.mount(
            "/static",
            StaticFiles(directory=str(frontend_dir)),
            name="static",
        )

        @app.get("/")
        async def serve_index():
            return FileResponse(str(frontend_dir / "index.html"))


    @app.get("/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "version": __version__,
            "environment": settings.environment,
        }

    logger.info(
        "app.started",
        version=__version__,
        environment=settings.environment,
        model=settings.anthropic_model,
        tools_registered=len(app.state.tool_registry.names()),
    )

    return app


app = create_app()
