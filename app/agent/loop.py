"""Agent loop - multi-iteration tool use with guards.

Loop invariants:
- Always terminates: end_turn, max_iterations, token_budget_exceeded, or error
- 'messages' list is the sole source of truth; no hidden state
- Tool errors surface as is_error tool_result blocks; the agent can recover
"""

from __future__ import annotations

import json
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
    """Orchestrates mulit-round tool use with Claude, bounded by iteration
    and token-budget guards."""

    def __init__(
            self,
            registry: ToolRegistry,
            client: AsyncAnthropic | None = None,
    ) -> None:
        settings = get_settings()
        self._registry = registry
        self._client = client or AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model
        self._max_iterations = settings.agent_max_iterations
        self._token_budget = settings.agent_max_tokens_budget
        self._max_tokens_per_call = 2048

    async def run(self, user_message: str) -> ChatResponse:
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        tool_calls: list[ToolCallRecord] = []
        total_input_tokens = 0
        total_output_tokens = 0
        iteration = 0
        stop_reason: StopReason = "end_turn"
        final_text = ""

        logger.info(
            "agent.run.start",
            message_length=len(user_message),
            max_iterations=self._max_iterations,
            token_budget=self._token_budget,
            )

        while iteration < self._max_iterations:
            iteration += 1

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
                "agent.iteration.completed",
                iteration=iteration,
                stop_reason=response.stop_reason,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cumulative_tokens=total_input_tokens + total_output_tokens,
            )

            # Token budget guard - check BEFORE continuing
            if total_input_tokens + total_output_tokens > self._token_budget:
                stop_reason = "token_budget_exceeded"
                final_text = self._extract_text(response.content) or (
                    "Token budget exceeded before I could complete the analysis. "
                    "Try a more focused question."
                )
                break

            # Model is done
            if response.stop_reason != "tool_use":
                stop_reason = "end_turn"
                final_text = self._extract_text(response.content)
                break

            # Model wants tools - execute them all, append results
            messages.append({"role": "assistant", "content": response.content})
            tool_result_blocks: list[dict[str, Any]] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                tool_result_blocks.append(
                    await self._execute_tool_block(block, iteration, tool_calls)
                )
            messages.append({"role": "user", "content": tool_result_blocks})

        else:
            # while-else: loop exhausted without break ->max_iterations
            stop_reason = "max_iterations"
            final_text = (
                "I reached my maximum number of tool-calling rounds before finishing. "
                "Here's what I learned so far — try asking a more specific follow-up."
            )

        logger.info(
            "agent.run.completed",
            iterations_used=iteration,
            stop_reason=stop_reason,
            tool_calls=len(tool_calls),
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
        )

        return ChatResponse(
            reply=final_text,
            tool_calls=tool_calls,
            iterations_used=iteration,
            stop_reason=stop_reason,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
        )

    async def _execute_tool_block(
            self, block: Any, iteration: int, tool_calls_log: list[ToolCallRecord]
    ) -> dict[str, Any]:
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
                    iteration=iteration,
                )
            )
            return {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, default=str),
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
                    iteration=iteration,
                )
            )
            return {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": error_msg,
                "is_error": True,
            }

    @staticmethod
    def _extract_text(content: list[Any]) -> str:
        return "".join(block.text for block in content if block.type == "text").strip()
