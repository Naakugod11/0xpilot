"""FastAPI application factory.

Composes config, middleware, observability, tools, and routes into a single
ASGI app. Use `create_app()` from uvicorn / Railway start command:

    uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.middleware import request_context_middleware
from app.api.routes import router as api_router
from app.config import get_settings
from app.observability.logger import get_logger, setup_logging
from app.observability.metrics import MetricsCollector
from app.tools.registry import build_default_registry

logger = get_logger(__name__)


def create_app() -> FastAPI:
    """Build and return a fully wired FastAPI app."""
    settings = get_settings()
    setup_logging()

    app = FastAPI(
        title="0xpilot",
        description="Autonomous Web3 research agent — Phase 3 of a 5-phase Web3+AI roadmap.",
        version="0.1.0",
    )

    # ─── Middleware ────────────────────────────────────────────
    # CORS: permissive for now (single deploy, same origin). Tighten
    # to specific allowed_origins once a custom domain is attached.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # Request-id propagation + structured logging per request
    app.middleware("http")(request_context_middleware)

    # ─── App state (singletons) ────────────────────────────────
    app.state.tool_registry = build_default_registry()
    app.state.metrics = MetricsCollector()

    logger.info(
        "app.startup.tools_registered",
        tool_count=len(app.state.tool_registry.names()),
        tools=app.state.tool_registry.names(),
    )

    # ─── Routes ────────────────────────────────────────────────
    app.include_router(api_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe used by Railway healthcheck + uptime monitors."""
        return {"status": "ok"}

    # ─── Frontend (static) ─────────────────────────────────────
    # Serve the brutalist terminal UI at /. Files live in `frontend/`.
    # In production (Railway) this is fine; for higher traffic move the
    # static asset to a CDN/edge.
    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
    if frontend_dir.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(frontend_dir)),
            name="static",
        )

        @app.get("/", include_in_schema=False)
        async def serve_index() -> FileResponse:
            return FileResponse(str(frontend_dir / "index.html"))

        logger.info("app.startup.frontend_mounted", path=str(frontend_dir))
    else:
        logger.warning(
            "app.startup.frontend_missing",
            expected_path=str(frontend_dir),
            note="API endpoints work, but no UI will be served at /",
        )

    return app


# Module-level app instance for uvicorn / Railway
app = create_app()