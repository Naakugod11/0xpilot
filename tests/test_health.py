"""Sanity tests for the application factory and health endpoint."""

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
    # Ensure the cached settings don't leak stale values between tests
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    os.environ.clear()
    os.environ.update(original)
    get_settings.cache_clear()


def test_health_endpoint_returns_ok() -> None:
    from app.main import create_app

    client = TestClient(create_app())
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "dev"
    assert "version" in body


def test_settings_require_anthropic_key() -> None:
    """Settings must fail loudly when a required key is missing."""
    from pydantic import ValidationError

    from app.config import Settings, get_settings

    get_settings.cache_clear()
    del os.environ["ANTHROPIC_API_KEY"]

    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_solana_not_configured_in_phase_3() -> None:
    """By default (no HELIUS_API_KEY), solana_configured is False.

    Phase 3.5 will flip this on. This test documents the phased rollout.
    """
    from app.config import Settings, get_settings

    get_settings.cache_clear()
    settings = Settings()  # type: ignore[call-arg]
    assert settings.solana_configured is False
    assert settings.telegram_configured is False
