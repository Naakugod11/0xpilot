"""FastAPI application entry point.

Step 1 scope: minimal app with /health endpoint.
Step 2 will add logging middleware + request_id propagation.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import get_settings


def create_app() -> FastAPI:
    """Application factory. Keeps main importable without side effects."""
    settings = get_settings()

    app = FastAPI(
        title="0xpilot",
        description="Autonomous Web3 research agent with tool use",
        version=__version__,
    )

    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "version": __version__,
            "environment": settings.environment,
        }

    return app


app = create_app()
