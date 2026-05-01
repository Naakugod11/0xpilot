"""Tests for MetricsCollector + /metrics endpoint."""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.observability.metrics import MetricsCollector


@pytest.fixture(autouse=True)
def _env_vars() -> Generator[None, None, None]:
    original = dict(os.environ)
    os.environ.update(
        {
            "ANTHROPIC_API_KEY": "test",
            "ALCHEMY_API_KEY": "test",
            "ZERION_API_KEY": "test",
            "COINGECKO_API_KEY": "test",
            "OXBRAIN_BASE_URL": "https://oxbrain.example.com",
        }
    )
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    os.environ.clear()
    os.environ.update(original)
    get_settings.cache_clear()


# ─── MetricsCollector unit tests ─────────────────────────────────────


async def test_record_tool_call_increments_counters() -> None:
    m = MetricsCollector()
    await m.record_tool_call("get_gas_price", duration_ms=42.5, success=True)
    await m.record_tool_call("get_gas_price", duration_ms=15.0, success=True)
    await m.record_tool_call("get_gas_price", duration_ms=99.0, success=False)

    snap = await m.snapshot()
    tool = snap["tools"]["get_gas_price"]
    assert tool["calls_total"] == 3
    assert tool["calls_success"] == 2
    assert tool["calls_error"] == 1
    assert tool["latency_ms"]["count"] == 3
    assert tool["latency_ms"]["p50"] is not None


async def test_record_agent_run_aggregates() -> None:
    m = MetricsCollector()
    await m.record_agent_run(
        iterations=2, tool_calls=3, input_tokens=1000, output_tokens=200,
        duration_ms=1500.0, stop_reason="end_turn",
    )
    await m.record_agent_run(
        iterations=4, tool_calls=2, input_tokens=2000, output_tokens=300,
        duration_ms=2500.0, stop_reason="end_turn",
    )

    snap = await m.snapshot()
    agent = snap["agent"]
    assert agent["runs_total"] == 2
    assert agent["iterations_total"] == 6
    assert agent["tool_calls_total"] == 5
    assert agent["input_tokens_total"] == 3000
    assert agent["output_tokens_total"] == 500
    assert agent["stop_reasons"] == {"end_turn": 2}


async def test_percentiles_with_few_samples() -> None:
    m = MetricsCollector()
    await m.record_tool_call("x", 10, success=True)
    await m.record_tool_call("x", 100, success=True)
    snap = await m.snapshot()
    lat = snap["tools"]["x"]["latency_ms"]
    assert lat["count"] == 2
    assert lat["p50"] in (10, 100)


async def test_reset_clears_all() -> None:
    m = MetricsCollector()
    await m.record_tool_call("x", 50, success=True)
    await m.record_agent_run(
        iterations=1, tool_calls=1, input_tokens=100, output_tokens=20,
        duration_ms=500, stop_reason="end_turn",
    )
    await m.reset()

    snap = await m.snapshot()
    assert snap["agent"]["runs_total"] == 0
    assert snap["tools"] == {}


async def test_latency_samples_are_capped() -> None:
    m = MetricsCollector()
    m._max_latency_samples = 5  # force tight cap for the test
    for i in range(10):
        await m.record_tool_call("x", float(i), success=True)

    snap = await m.snapshot()
    assert snap["tools"]["x"]["latency_ms"]["count"] == 5
    # Should keep the most recent — values 5..9
    samples = m._tool_latencies["x"]
    assert min(samples) == 5
    assert max(samples) == 9


# ─── /metrics endpoint integration ───────────────────────────────────


def test_metrics_endpoint_returns_initial_snapshot() -> None:
    from app.main import create_app

    client = TestClient(create_app())
    response = client.get("/metrics")
    assert response.status_code == 200

    body = response.json()
    assert "uptime_seconds" in body
    assert "agent" in body
    assert "tools" in body
    assert body["agent"]["runs_total"] == 0
