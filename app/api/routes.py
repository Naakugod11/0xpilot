"""HTTP routes for non-chat endpoints (tools listing) and the /chat endpoint."""

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


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    """Single-turn chat with the agent. Step 5: one round of tool use max."""
    registry = request.app.state.tool_registry
    loop = AgentLoop(registry=registry)

    try:
        return await loop.run(payload.message)
    except Exception as exc:
        # Let the middleware log it; return a clean 500 to the client
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc