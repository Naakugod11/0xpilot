"""Smoke test for /chat/stream SSE endpoint."""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env_vars() -> Generator[None, None, None]:
    original = dict(os.environ)
    os.environ.update(
        {
            "ANTHROPIC_API_KEY": "test",
            "ALCHEMY_API_KEY": "test",
            "ZERION_API_KEY": "test",
            "COINGECKO_API_KEY": "test",
            "OXBRAIN_BASE_URL": "https://oxbrain.example.com",
        }
    )
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    os.environ.clear()
    os.environ.update(original)
    get_settings.cache_clear()


def test_chat_stream_endpoint_exists() -> None:
    """Sanity: stream endpoint accepts requests and returns SSE content type.

    We don't have a real Anthropic key in tests so the agent will error,
    but the error itself comes back as an SSE 'error' event — which
    proves the endpoint and SSE wiring work.
    """
    from app.main import create_app

    client = TestClient(create_app())
    response = client.post("/chat/stream", json={"message": "test"})

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"].lower()
    # Must contain at least one SSE frame
    assert "data: " in response.text


def test_static_index_served() -> None:
    """If the frontend dir exists, GET / should return HTML."""
    from app.main import create_app

    client = TestClient(create_app())
    response = client.get("/")

    # If frontend dir exists, expect HTML; otherwise 404 is acceptable
    if response.status_code == 200:
        assert "html" in response.headers["content-type"].lower()
        assert "0xpilot" in response.text.lower()
