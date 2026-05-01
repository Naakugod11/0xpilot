"""In-memory metrics collector.

Keeps running counters + latency histograms accessible via /metrics.
Single-process / single-worker simple version. For multi-worker prod,
swap in Prometheus client library - same call sites, different backend.

Thread-safety: asyncio.Lock around mutations. Reads snapshot atomically.
"""

from __future__ import annotations

import asyncio
import statistics
from collections import defaultdict
from time import time
from typing import Any


class MetricsCollector:
    """Aggregates tool, request, and token counters for /metrics."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

        # Tool metrics
        # tool_name -> count
        self._tool_calls_total: dict[str, int] = defaultdict(int)
        self._tool_calls_success: dict[str, int] = defaultdict(int)
        self._tool_calls_error: dict[str, int] = defaultdict(int)
        # tool_name -> [latency_ms, ...]  (capped to last 1000 to bound memory)
        self._tool_latencies: dict[str, list[float]] = defaultdict(list)
        self._max_latency_samples = 1000

        # Agent run metrics
        self._agent_runs_total: int = 0
        self._agent_iterations_total: int = 0
        self._agent_tool_calls_total: int = 0
        self._agent_input_tokens_total: int = 0
        self._agent_output_tokens_total: int = 0
        self._agent_run_latencies: list[float] = []
        # stop_reason -> count
        self._agent_stop_reasons: dict[str, int] = defaultdict(int)

        self._started_at = time()

    # Recording API

    async def record_tool_call(
            self, tool_name: str, duration_ms: float, success: bool
    ) -> None:
        async with self._lock:
            self._tool_calls_total[tool_name] += 1
            if success:
                self._tool_calls_success[tool_name] += 1
            else:
                self._tool_calls_error[tool_name] += 1

            samples = self._tool_latencies[tool_name]
            samples.append(duration_ms)
            if len(samples) > self._max_latency_samples:
                # Keep most recent N
                del samples[: len(samples) - self._max_latency_samples]

    async def record_agent_run(
            self,
            iterations: int,
            tool_calls: int,
            input_tokens: int,
            output_tokens: int,
            duration_ms: float,
            stop_reason: str,
    ) -> None:
        async with self._lock:
            self._agent_runs_total += 1
            self._agent_iterations_total += iterations
            self._agent_tool_calls_total += tool_calls
            self._agent_input_tokens_total += input_tokens
            self._agent_output_tokens_total += output_tokens
            self._agent_run_latencies.append(duration_ms)
            if len(self._agent_run_latencies) > self._max_latency_samples:
                del self._agent_run_latencies[
                    : len(self._agent_run_latencies) - self._max_latency_samples
                ]
            self._agent_stop_reasons[stop_reason] += 1

    # Snapshot API (called by /metrics)

    @staticmethod
    def _percentiles(samples: list[float]) -> dict[str, float | None]:
        if not samples:
            return {"p50": None, "p95": None, "p99": None, "count": 0}
        srt = sorted(samples)
        return {
            "count": len(srt),
            "p50": round(srt[int(len(srt) * 0.50)], 2),
            "p95": round(srt[min(int(len(srt) * 0.95), len(srt) - 1)], 2),
            "p99": round(srt[min(int(len(srt) * 0.99), len(srt) - 1)], 2),
            "mean": round(statistics.mean(srt), 2),
        }

    async def snapshot(self) -> dict[str, Any]:
        """Atomic read of all metrics. Safe to call from /metrics endpoint."""
        async with self._lock:
            tool_breakdown: dict[str, Any] = {}
            for name in self._tool_calls_total:
                tool_breakdown[name] = {
                    "calls_total": self._tool_calls_total[name],
                    "calls_success": self._tool_calls_success[name],
                    "calls_error": self._tool_calls_error[name],
                    "latency_ms": self._percentiles(self._tool_latencies[name]),
                }

            return {
                "uptime_seconds": round(time() - self._started_at, 1),
                "agent": {
                    "runs_total": self._agent_runs_total,
                    "iterations_total": self._agent_iterations_total,
                    "tool_calls_total": self._agent_tool_calls_total,
                    "input_tokens_total": self._agent_input_tokens_total,
                    "output_tokens_total": self._agent_output_tokens_total,
                    "run_latency_ms": self._percentiles(self._agent_run_latencies),
                    "stop_reasons": dict(self._agent_stop_reasons),
                },
                "tools": tool_breakdown,
            }

    # Test/Admin helper

    async def reset(self) -> None:
        async with self._lock:
            self._tool_calls_total.clear()
            self._tool_calls_success.clear()
            self._tool_calls_error.clear()
            self._tool_latencies.clear()
            self._agent_runs_total = 0
            self._agent_iterations_total = 0
            self._agent_tool_calls_total = 0
            self._agent_input_tokens_total = 0
            self._agent_output_tokens_total = 0
            self._agent_run_latencies.clear()
            self._agent_stop_reasons.clear()
            self._started_at = time()
