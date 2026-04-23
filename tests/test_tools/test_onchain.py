"""Unit tests for on-chain tools. Client is mocked — we test tool logic only."""

from __future__ import annotations

import os
from collections.abc import Generator
from unittest.mock import AsyncMock

import pytest

from app.clients.alchemy import AlchemyClient
from app.tools.onchain import GasPriceTool


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


async def test_gas_price_tool_converts_wei_to_gwei() -> None:
    mock_client = AsyncMock(spec=AlchemyClient)
    mock_client.get_gas_price.return_value = 25_500_000_000  # 25.5 gwei in wei

    tool = GasPriceTool(client=mock_client)
    result = await tool.execute(chain="ethereum")

    assert result == {
        "chain": "ethereum",
        "gas_price_gwei": 25.5,
        "gas_price_wei": 25_500_000_000,
    }
    mock_client.get_gas_price.assert_awaited_once_with("ethereum")


async def test_gas_price_tool_exposes_anthropic_schema() -> None:
    tool = GasPriceTool(client=AsyncMock(spec=AlchemyClient))
    schema = tool.to_anthropic_schema()

    assert schema["name"] == "get_gas_price"
    assert "description" in schema
    assert schema["input_schema"]["required"] == ["chain"]
    # chain enum should include our first-class chains
    chain_enum = schema["input_schema"]["properties"]["chain"]["enum"]
    assert "ethereum" in chain_enum
    assert "base" in chain_enum
