"""Base abstract class for all tools.

every tool in 0xpilot implements this interface. The Anthropic API needs:
- 'name' - unique identifier the LLM uses to invoke the tool
- 'description' natural language, tells the LLM when to use it
- 'input_schema' - JSON schema the LLM must conform to when calling

We add:
- 'async execute(**kwargs) -> dict' - the actual work
- 'to_anthropic_schema()' - serialize for the tools= param of the API call
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class BaseTool(ABC):
    """Abstract base for all agent tools.

    Subclasses must set `name`, `description`, `input_schema` as class attrs,
    and implement `execute()`.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, Any]]

    @abstractmethod
    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Run the tool. Return value will be JSON-serialized and sent back
        to the LLM as a tool_result block.

        Raise any exception for tool failure - the agent loop catches it and
        converts to an is_error=true tool_Result so the LLM can recover.
        """
        ...

    def to_anthropic_schema(self) -> dict[str, Any]:
        """Serialize this tool's contract for the Anthropic API 'tools=' param."""
        return{
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
