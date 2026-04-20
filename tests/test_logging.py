"""Tests for request-id middleware and structured logging."""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env_vars() -> Generator[None, None, None]:
    """Populate required env vars so Settings() doesn't explode in tests."""
    original = dict(os.environ)
    os.environ.update(
        {
            "ANTHROPIC_API_KEY": "test-anthropic",
            "ALCHEMY_API_KEY": "test-alchemy",
            "ZERION_API_KEY": "test-zerion",
            "COINGECKO_API_KEY": "test-coingecko",
            "OXBRAIN_BASE_URL": "https://oxbrain.example.com",
            "ENVIRONMENT": "dev",
        }
    )
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    os.environ.clear()
    os.environ.update(original)
    get_settings.cache_clear()


def test_response_echoes_request_id_header() -> None:
    """Middleware must echo X-Request-ID back to the client."""
    from app.main import create_app

    client = TestClient(create_app())
    response = client.get("/health")

    assert response.status_code == 200
    assert "x-request-id" in {k.lower() for k in response.headers}
    request_id = response.headers["x-request-id"]
    assert len(request_id) > 0


def test_response_preserves_inbound_request_id() -> None:
    """If client sends X-Request-ID, middleware must reuse it (for tracing)."""
    from app.main import create_app

    client = TestClient(create_app())
    custom_id = "my-custom-trace-id-123"
    response = client.get("/health", headers={"X-Request-ID": custom_id})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == custom_id


def test_each_request_gets_unique_request_id() -> None:
    """Different requests must get different auto-generated request_ids."""
    from app.main import create_app

    client = TestClient(create_app())
    r1 = client.get("/health")
    r2 = client.get("/health")

    assert r1.headers["x-request-id"] != r2.headers["x-request-id"]
