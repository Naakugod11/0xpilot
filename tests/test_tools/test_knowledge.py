"""Unit tests for ENS + 0xbrain knowledge tools."""

from __future__ import annotations

import os
from collections.abc import Generator
from unittest.mock import AsyncMock

import pytest

from app.clients.alchemy import AlchemyClient
from app.clients.oxbrain import OxbrainClient
from app.tools.knowledge import Query0xbrainTool, ResolveEnsTool


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


# ─── ResolveEnsTool ──────────────────────────────────────────────────


async def test_ens_forward_resolution() -> None:
    mock = AsyncMock(spec=AlchemyClient)
    mock.resolve_ens_name_to_address.return_value = (
        "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
    )

    tool = ResolveEnsTool(client=mock)
    result = await tool.execute(name="vitalik.eth")

    assert result["found"] is True
    assert result["direction"] == "forward"
    assert result["address"] == "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"


async def test_ens_forward_unresolved() -> None:
    mock = AsyncMock(spec=AlchemyClient)
    mock.resolve_ens_name_to_address.return_value = None

    tool = ResolveEnsTool(client=mock)
    result = await tool.execute(name="nonexistent.eth")

    assert result["found"] is False
    assert result["address"] is None


async def test_ens_reverse_resolution() -> None:
    mock = AsyncMock(spec=AlchemyClient)
    mock.reverse_resolve_ens.return_value = "vitalik.eth"

    tool = ResolveEnsTool(client=mock)
    result = await tool.execute(address="0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")

    assert result["found"] is True
    assert result["direction"] == "reverse"
    assert result["primary_name"] == "vitalik.eth"


async def test_ens_no_input_returns_error() -> None:
    mock = AsyncMock(spec=AlchemyClient)
    tool = ResolveEnsTool(client=mock)

    result = await tool.execute()

    assert result["found"] is False
    assert "Provide either" in result["error"]


# ─── Query0xbrainTool ────────────────────────────────────────────────


async def test_query_0xbrain_returns_answer_and_sources() -> None:
    mock = AsyncMock(spec=OxbrainClient)
    mock.query.return_value = {
        "question": "how do uniswap v4 hooks work?",
        "answer": "Uniswap V4 introduces hooks, which allow custom logic...",
        "sources": [
            {
                "title": "Uniswap V4 Whitepaper",
                "content_snippet": "Hooks are contracts that execute at specific lifecycle points.",
                "relevance_score": 0.87,
            },
            {
                "title": "V4 Core Reference",
                "content_snippet": "Each hook is identified by a flag bit pattern.",
                "relevance_score": 0.71,
            },
        ],
    }

    tool = Query0xbrainTool(client=mock)
    result = await tool.execute(question="how do uniswap v4 hooks work?")

    assert result["question"] == "how do uniswap v4 hooks work?"
    assert "hooks" in result["answer"].lower()
    assert result["sources_count"] == 2
    assert result["sources"][0]["title"] == "Uniswap V4 Whitepaper"
    assert result["sources"][0]["relevance_score"] == 0.87


async def test_query_0xbrain_respects_top_k_and_category() -> None:
    mock = AsyncMock(spec=OxbrainClient)
    mock.query.return_value = {
        "question": "test",
        "answer": "...",
        "sources": [
            {"title": f"doc {i}", "content_snippet": "...", "relevance_score": 0.5}
            for i in range(10)
        ],
    }

    tool = Query0xbrainTool(client=mock)
    result = await tool.execute(question="test", top_k=3, category_filter="defi")

    assert result["sources_count"] == 3
    assert result["category_filter_applied"] == "defi"
    mock.query.assert_awaited_once_with("test", top_k=3, category_filter="defi")