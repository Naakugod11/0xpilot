"""HTTP routes for non-chat endpoints (tools listing, metrics) and /chat."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.agent.loop import AgentLoop
from app.agent.schemas import ChatRequest, ChatResponse

router = APIRouter()


@router.get("/tools")
async def list_tools(request: Request) -> dict[str, Any]:
    """Return the full tool catalog as Anthropic-compatible schemas."""
    registry = request.app.state.tool_registry
    return {
        "count": len(registry.names()),
        "tools": registry.get_anthropic_schemas(),
    }


@router.get("/metrics")
async def get_metrics(request: Request) -> dict[str, Any]:
    """Aggregated runtime metrics: agent runs, tool latencies, token usage."""
    metrics = request.app.state.metrics
    return await metrics.snapshot()


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    """Single-turn chat with the agent."""
    registry = request.app.state.tool_registry
    metrics = request.app.state.metrics
    loop = AgentLoop(registry=registry, metrics=metrics)

    try:
        return await loop.run(payload.message)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc
