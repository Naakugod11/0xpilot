"""Tests for AgentLoop. Anthropic API is mocked — we test loop logic only."""

from __future__ import annotations

import os
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agent.loop import AgentLoop
from app.agent.schemas import ChatResponse
from app.tools.base import BaseTool
from app.tools.registry import ToolRegistry


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


# ─── Fake Anthropic SDK response shapes ──────────────────────────────


@dataclass
class _Usage:
    input_tokens: int = 10
    output_tokens: int = 20


@dataclass
class _TextBlock:
    text: str
    type: str = "text"


@dataclass
class _ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class _FakeResponse:
    content: list[Any]
    stop_reason: str
    usage: _Usage


# ─── Test tool ───────────────────────────────────────────────────────


class _EchoTool(BaseTool):
    name = "echo"
    description = "echoes input"
    input_schema = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
    }

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        return {"echoed": kwargs.get("msg")}


# ─── Tests ───────────────────────────────────────────────────────────


async def test_simple_reply_no_tools() -> None:
    """LLM returns text directly without calling any tools."""
    mock_anthropic = AsyncMock()
    mock_anthropic.messages.create.return_value = _FakeResponse(
        content=[_TextBlock(text="Hello, trader.")],
        stop_reason="end_turn",
        usage=_Usage(),
    )

    registry = ToolRegistry()
    loop = AgentLoop(registry=registry, client=mock_anthropic)

    result = await loop.run("hi")

    assert isinstance(result, ChatResponse)
    assert result.reply == "Hello, trader."
    assert result.tool_calls == []
    assert result.iterations_used == 1
    assert result.stop_reason == "end_turn"
    assert result.input_tokens == 10
    assert result.output_tokens == 20


async def test_single_tool_use_then_final_text() -> None:
    """LLM requests a tool, we dispatch, LLM replies with final text."""
    registry = ToolRegistry()
    registry.register(_EchoTool())

    mock_anthropic = AsyncMock()
    # Turn 1: model requests echo tool
    # Turn 2: model returns final text
    mock_anthropic.messages.create.side_effect = [
        _FakeResponse(
            content=[_ToolUseBlock(id="tu_1", name="echo", input={"msg": "hi"})],
            stop_reason="tool_use",
            usage=_Usage(input_tokens=15, output_tokens=25),
        ),
        _FakeResponse(
            content=[_TextBlock(text="The echo said: hi")],
            stop_reason="end_turn",
            usage=_Usage(input_tokens=30, output_tokens=10),
        ),
    ]

    loop = AgentLoop(registry=registry, client=mock_anthropic)
    result = await loop.run("echo hi please")

    assert result.reply == "The echo said: hi"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "echo"
    assert result.tool_calls[0].arguments == {"msg": "hi"}
    assert result.tool_calls[0].result == {"echoed": "hi"}
    assert result.tool_calls[0].error is None
    assert result.iterations_used == 2
    assert result.input_tokens == 45
    assert result.output_tokens == 35


async def test_tool_failure_captured_as_error_in_record() -> None:
    """When a tool raises, the error is captured; loop continues, returns final text."""

    class _Broken(BaseTool):
        name = "broken"
        description = "always fails"
        input_schema = {"type": "object", "properties": {}}

        async def execute(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("kaboom")

    registry = ToolRegistry()
    registry.register(_Broken())

    mock_anthropic = AsyncMock()
    mock_anthropic.messages.create.side_effect = [
        _FakeResponse(
            content=[_ToolUseBlock(id="tu_1", name="broken", input={})],
            stop_reason="tool_use",
            usage=_Usage(),
        ),
        _FakeResponse(
            content=[_TextBlock(text="Tool failed, here's what I can say: ...")],
            stop_reason="end_turn",
            usage=_Usage(),
        ),
    ]

    loop = AgentLoop(registry=registry, client=mock_anthropic)
    result = await loop.run("call broken")

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].error is not None
    assert "kaboom" in result.tool_calls[0].error
    assert result.tool_calls[0].result is None
    assert result.reply.startswith("Tool failed")