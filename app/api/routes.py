"""HTTP routes for non-chat endpoints (tools listing, etc.).

The main /chat endpoint comes in Step 5 once the agent loop exists.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/tools")
async def list_tools(request: Request) -> dict[str, Any]:
    """Return the full tool catalog as Anthropic-compatible schemas.

    Useful for debugging + frontend demos that show "here's what the agent can do".
    """
    registry = request.app.state.tool_registry
    return {
        "count": len(registry.names()),
        "tools": registry.get_anthropic_schemas(),
    }