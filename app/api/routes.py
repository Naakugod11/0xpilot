"""HTTP routes — chat (sync), chat/stream (SSE), tools, metrics."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.agent.loop import AgentLoop
from app.agent.schemas import ChatRequest, ChatResponse

router = APIRouter()


@router.get("/tools")
async def list_tools(request: Request) -> dict[str, Any]:
    registry = request.app.state.tool_registry
    return {
        "count": len(registry.names()),
        "tools": registry.get_anthropic_schemas(),
    }


@router.get("/metrics")
async def get_metrics(request: Request) -> dict[str, Any]:
    metrics = request.app.state.metrics
    return await metrics.snapshot()


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    """Single-turn chat (non-streaming)."""
    registry = request.app.state.tool_registry
    metrics = request.app.state.metrics
    loop = AgentLoop(registry=registry, metrics=metrics)

    try:
        return await loop.run(payload.message)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    """Streaming chat via Server-Sent Events.

    Each event is encoded as SSE 'data: <json>\\n\\n'. Frontend parses
    incrementally and updates UI as iterations + tool calls arrive.
    """
    registry = request.app.state.tool_registry
    metrics = request.app.state.metrics
    loop = AgentLoop(registry=registry, metrics=metrics)

    async def event_stream():
        try:
            async for event in loop.run_streaming(payload.message):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as exc:
            err = {"event": "error", "message": f"{type(exc).__name__}: {exc}"}
            yield f"data: {json.dumps(err)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering
        },
    )
