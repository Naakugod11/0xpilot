"""Pydantic models for the /chat request/response contract."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)

class ToolCallRecord(BaseModel):
    """What happened during a single tool invocation. Surfaced to the client
    so the UI can show a timeline of what the agent did."""

    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: float

StopReason = Literal["end_turn", "max_iterations", "error"]

class ChatResponse(BaseModel):
    """Full agent run output."""

    reply: str
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    iterations_used: int
    stop_reason: StopReason
    input_tokens: int
    output_tokens: int