"""Tool registry - single source of truth for available tools.

Lifecycle:
1. Subclass BaseTool, set name/description/input_schema, implement execute()
2. Register an instance with 'registry.register(MyTool())'
3. The agent loop calls 'registry.get_anthropic_schemas()' when building the
    API request, and 'registry.dispatch(name, args, request_id)' when the
    LLM emits a tool_use block.

dispatch() handles logging + timing + error normalization so tools themselves
can stay focused on their actual work.
"""

from __future__ import annotations

import time
from typing import Any

from app.observability.logger import get_logger
from app.tools.base import BaseTool

logger = get_logger(__name__)

class ToolNotFoundError(Exception):
    """Raised when the LLM tries to call a tool that isn't registered."""

class ToolRegistry:
    """Registry of all tools available to the agent."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool. Raises if a tool with the same name already exists."""
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} already registered")
        self._tools[tool.name] = tool
        logger.info("tool.registered", name=tool.name)

    def get(self, name: str) -> BaseTool:
        if name not in self._tools:
            raise ToolNotFoundError(f"No tool registered with name {name!r}")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def get_anthropic_schemas(self) -> list[dict[str, Any]]:
        """Serialize all tools for the Anthropic API 'tools=' parameter."""
        return[tool.to_anthropic_schema() for tool in self._tools.values()]

    async def dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool by name. Logs timing + status. Raises on unknown tool.

        Returns the tool's raw output dict. caller (the agent loop) are
        responsible for wrapping exceptions into is_error tool_result blocks.
        """
        tool = self.get(name)
        start = time.perf_counter()

        logger.info("tool.dispatch.start", tool=name, args=args)
        try:
            result = await tool.execute(**args)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.exception(
                "tool.dispatch.error",
                tool=name,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
            )
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info("tool.dispatch.success", tool=name, duration_ms=duration_ms)
        return result

def build_default_registry() -> ToolRegistry:
    """Instantiate the registry with all production tools wired up."""
    from app.tools.market import (
        GetTokenOverviewTool,
        GetTokenSocialStatsTool,
        ScanNewPairsTool,
    )
    from app.tools.onchain import GasPriceTool
    from app.tools.security import GetHolderDistributionTool, GetTokenSecurityTool

    registry = ToolRegistry()
    registry.register(GasPriceTool())
    registry.register(GetTokenOverviewTool())
    registry.register(ScanNewPairsTool())
    registry.register(GetTokenSocialStatsTool())
    registry.register(GetTokenSecurityTool())
    registry.register(GetHolderDistributionTool())
    return registry
