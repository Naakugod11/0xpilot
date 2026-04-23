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
            "AGENT_MAX_ITERATIONS": "5",
            "AGENT_MAX_TOKENS_BUDGET": "5000",
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


# ─── Test tools ──────────────────────────────────────────────────────


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


class _BrokenTool(BaseTool):
    name = "broken"
    description = "always fails"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("kaboom")


# ─── Tests: single iteration (Step 5 regression) ─────────────────────


async def test_simple_reply_no_tools() -> None:
    mock_anthropic = AsyncMock()
    mock_anthropic.messages.create.return_value = _FakeResponse(
        content=[_TextBlock(text="Hello, trader.")],
        stop_reason="end_turn",
        usage=_Usage(),
    )

    loop = AgentLoop(registry=ToolRegistry(), client=mock_anthropic)
    result = await loop.run("hi")

    assert isinstance(result, ChatResponse)
    assert result.reply == "Hello, trader."
    assert result.tool_calls == []
    assert result.iterations_used == 1
    assert result.stop_reason == "end_turn"


async def test_single_tool_use_then_final_text() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())

    mock_anthropic = AsyncMock()
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
    assert result.tool_calls[0].iteration == 1
    assert result.iterations_used == 2


async def test_tool_failure_captured_as_error_in_record() -> None:
    registry = ToolRegistry()
    registry.register(_BrokenTool())

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


# ─── Tests: multi-iteration (Step 6 new) ─────────────────────────────


async def test_multi_round_tool_chaining() -> None:
    """Agent calls tool → sees result → calls another tool → sees result → ends."""
    registry = ToolRegistry()
    registry.register(_EchoTool())

    mock_anthropic = AsyncMock()
    mock_anthropic.messages.create.side_effect = [
        # iter 1: tool call
        _FakeResponse(
            content=[_ToolUseBlock(id="t1", name="echo", input={"msg": "first"})],
            stop_reason="tool_use",
            usage=_Usage(),
        ),
        # iter 2: another tool call based on first result
        _FakeResponse(
            content=[_ToolUseBlock(id="t2", name="echo", input={"msg": "second"})],
            stop_reason="tool_use",
            usage=_Usage(),
        ),
        # iter 3: final answer
        _FakeResponse(
            content=[_TextBlock(text="Both echoes processed.")],
            stop_reason="end_turn",
            usage=_Usage(),
        ),
    ]

    loop = AgentLoop(registry=registry, client=mock_anthropic)
    result = await loop.run("do two echoes in sequence")

    assert result.reply == "Both echoes processed."
    assert len(result.tool_calls) == 2
    assert result.tool_calls[0].arguments == {"msg": "first"}
    assert result.tool_calls[0].iteration == 1
    assert result.tool_calls[1].arguments == {"msg": "second"}
    assert result.tool_calls[1].iteration == 2
    assert result.iterations_used == 3
    assert result.stop_reason == "end_turn"


async def test_max_iterations_stops_loop() -> None:
    """If the model keeps requesting tools forever, we stop at max_iterations."""
    registry = ToolRegistry()
    registry.register(_EchoTool())

    mock_anthropic = AsyncMock()
    # Always return tool_use — model never gives final text
    mock_anthropic.messages.create.return_value = _FakeResponse(
        content=[_ToolUseBlock(id="t", name="echo", input={"msg": "again"})],
        stop_reason="tool_use",
        usage=_Usage(),
    )

    loop = AgentLoop(registry=registry, client=mock_anthropic)
    result = await loop.run("infinite loop scenario")

    assert result.stop_reason == "max_iterations"
    assert result.iterations_used == 5  # from env var
    assert len(result.tool_calls) == 5
    assert "maximum number of tool-calling rounds" in result.reply


async def test_token_budget_exceeded_stops_loop() -> None:
    """When cumulative tokens exceed budget, we break with the budget reason."""
    registry = ToolRegistry()
    registry.register(_EchoTool())

    mock_anthropic = AsyncMock()
    # First call already puts us over budget (3000 + 3000 > 5000)
    mock_anthropic.messages.create.return_value = _FakeResponse(
        content=[_TextBlock(text="partial answer before budget blew")],
        stop_reason="end_turn",
        usage=_Usage(input_tokens=3000, output_tokens=3000),
    )

    loop = AgentLoop(registry=registry, client=mock_anthropic)
    result = await loop.run("expensive query")

    assert result.stop_reason == "token_budget_exceeded"
    assert result.input_tokens == 3000
    assert result.output_tokens == 3000
