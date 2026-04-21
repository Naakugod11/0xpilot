"""Tests for ToolRegistry: registration, dispatch, schema serialization."""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.tools.base import BaseTool
from app.tools.registry import ToolNotFoundError, ToolRegistry


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


class _EchoTool(BaseTool):
    """Minimal test tool that echoes its kwargs."""

    name = "echo"
    description = "Echoes input for testing."
    input_schema = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
    }

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        return {"echoed": kwargs}


class _BrokenTool(BaseTool):
    name = "broken"
    description = "Always raises."
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("intentional failure")


async def test_register_and_dispatch_roundtrip() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())

    result = await registry.dispatch("echo", {"msg": "hello"})

    assert result == {"echoed": {"msg": "hello"}}


def test_duplicate_registration_raises() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_EchoTool())


async def test_dispatch_unknown_tool_raises() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        await registry.dispatch("nope", {})


async def test_dispatch_propagates_tool_exceptions() -> None:
    """Tool exceptions must propagate — the agent loop handles them, not the registry."""
    registry = ToolRegistry()
    registry.register(_BrokenTool())

    with pytest.raises(RuntimeError, match="intentional failure"):
        await registry.dispatch("broken", {})


def test_anthropic_schemas_for_all_tools() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())

    schemas = registry.get_anthropic_schemas()

    assert len(schemas) == 1
    assert schemas[0]["name"] == "echo"
    assert schemas[0]["input_schema"]["required"] == ["msg"]


def test_names_returns_sorted() -> None:
    class _A(_EchoTool):
        name = "alpha"

    class _Z(_EchoTool):
        name = "zeta"

    registry = ToolRegistry()
    registry.register(_Z())
    registry.register(_A())

    assert registry.names() == ["alpha", "zeta"]


# ─── /tools endpoint integration test ─────────────────────────────

def test_tools_endpoint_lists_registered_tools() -> None:
    from app.main import create_app

    client = TestClient(create_app())
    response = client.get("/tools")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 1
    tool_names = [t["name"] for t in body["tools"]]
    assert "get_gas_price" in tool_names