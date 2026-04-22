"""Agent loop - single iteration tool use.

Step 5 scope: ONE round of tool use. User asks -> LLM calls tools -> we dispatch
-> LLM sees results -> LLM returns final text.

Step 6 will extend this to multi-round (LLM can chain tool calls).
"""

from __future__ import annotations

import time
from typing import Any

from anthropic import AsyncAnthropic

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.schemas import ChatResponse, StopReason, ToolCallRecord
from app.config import get_settings
from app.observability.logger import get_logger
from app.tools.registry import ToolRegistry

logger = get_logger(__name__)

class AgentLoop:
    """Orchestrates one or more rounds of tool use with Claude."""

    def __init__(
            self,
            registry: ToolRegistry,
            client: AsyncAnthropic | None = None,
    ) -> None:
        settings = get_settings()
        self._registry = registry
        self._client = client or AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model
        self._max_tokens_per_call = 2048

    async def run(self, user_message: str) -> ChatResponse:
        """Execute the agent loop. Single iterations for Step 5."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        tool_calls: list[ToolCallRecord] = []
        total_input_tokens = 0
        total_output_tokens = 0

        logger.info("agent.run.start", message_length=len(user_message))

        # -- Turn 1: let the model think + optionally request tool use --
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens_per_call,
            system=SYSTEM_PROMPT,
            tools=self._registry.get_anthropic_schemas(),
            messages=messages,
        )
        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

        logger.info(
            "agent.turn.completed",
            turn=1,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

        # If the model didn't call any tool we're done
        if response.stop_reason != "tool_use":
            reply = self._extract_text(response.content)
            return ChatResponse(
                reply=reply,
                tool_calls=tool_calls,
                iterations_used=1,
                stop_reason=self._map_stop_reason(response.stop_reason),
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
            )
        # -- Execute every tool_use block the model emitted --
        # Append the assitant's turn verbatim to preserve context
        messages.append({"role": "assistant", "content": response.content})

        tool_result_blocks: list[dict[str, Any]] = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            tool_result_blocks.append(
                await self._execute_tool_block(block, tool_calls)
            )
        
        messages.append({"role": "user", "content": tool_result_blocks})

        # -- Turn 2: send tool results back, get final text --
        final = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens_per_call,
            system=SYSTEM_PROMPT,
            tools=self._registry.get_anthropic_schemas(),
            messages=messages,
        )
        total_input_tokens += final.usage.input_tokens
        total_output_tokens += final.usage.output_tokens

        logger.info(
            "agent.turn.completed",
            turn=2,
            stop_reason=final.stop_reason,
            input_tokens=final.usage.input_tokens,
            output_tokens=final.usage.output_tokens,
        )

        reply = self._extract_text(final.content)

        logger.info(
            "agent.run.completed",
            tool_calls=len(tool_calls),
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
        )

        return ChatResponse(
            reply=reply,
            tool_calls=tool_calls,
            iterations_used=2,
            stop_reason=self._map_stop_reason(final.stop_reason),
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
        )
    async def _execute_tool_block(
            self, block: Any, tool_calls_log: list[ToolCallRecord]
    ) -> dict[str, Any]:
        """Dispatch a single tool_use block; append result to log; return the
        tool_Result block for the next API call."""
        start = time.perf_counter()
        tool_name = block.name
        args = block.input

        try:
            result = await self._registry.dispatch(tool_name, args)
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            tool_calls_log.append(
                ToolCallRecord(
                    tool_name=tool_name,
                    arguments=args,
                    result=result,
                    duration_ms=duration_ms,
                )
            )
            return {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": self._serialize_for_llm(result),
            }
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            error_msg = f"{type(exc).__name__}: {exc}"
            tool_calls_log.append(
                ToolCallRecord(
                    tool_name=tool_name,
                    arguments=args,
                    error=error_msg,
                    duration_ms=duration_ms,
                )
            )
            # Return is_error=True so the LLM can recover / explain / retry
            return {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": error_msg,
                "is_error": True,
            }
        
    @staticmethod
    def _extract_text(content: list[Any]) -> str:
        """Concentrate text blocks from a model response."""
        return "".join(block.text for block in content if block.type == "text").strip()
    
    @staticmethod
    def _serialize_for_llm(result: dict[str, Any]) -> str:
        """Tool outputs are dicts; the API expects a string content in tool_result.
        JSON-encoding keeps structure while being LLM-readable."""
        import json

        return json.dumps(result, default=str)
    
    @staticmethod
    def _map_stop_reason(api_reason: str | None) -> StopReason:
        """Map Anthropic stop_reason to our StopReason literal."""
        if api_reason == "end_turn":
            return "end_turn"
        if api_reason == "max_tokens":
            return "error" # treat as error in single-iteration mode
        return "end_turn" # default fallback