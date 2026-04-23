"""Unit tests for Dexscreener-backed market tools."""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.clients.dexscreener import DexscreenerClient
from app.tools.market import (
    GetTokenOverviewTool,
    GetTokenSocialStatsTool,
    ScanNewPairsTool,
)


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


# ─── Fixtures ────────────────────────────────────────────────────────


def _pair(
    addr: str = "0xpair",
    liquidity_usd: float = 100_000.0,
    symbol: str = "PEPE",
    with_info: bool = True,
) -> dict[str, Any]:
    """Build a realistic pair dict matching Dexscreener's v1 shape."""
    data: dict[str, Any] = {
        "chainId": "base",
        "dexId": "uniswap",
        "pairAddress": addr,
        "baseToken": {"address": "0xbase", "name": "Pepe", "symbol": symbol},
        "quoteToken": {"address": "0xquote", "symbol": "WETH"},
        "priceUsd": "0.0000012",
        "liquidity": {"usd": liquidity_usd, "base": 1000, "quote": 10},
        "volume": {"h24": 250_000, "h1": 8_000},
        "priceChange": {"m5": 1.2, "h1": 5.5, "h24": 42.0},
        "fdv": 1_200_000,
        "marketCap": 800_000,
        "pairCreatedAt": 1_700_000_000_000,
        "url": "https://dexscreener.com/base/0xpair",
    }
    if with_info:
        data["info"] = {
            "imageUrl": "https://img.example/pepe.png",
            "websites": [{"url": "https://pepe.example"}],
            "socials": [
                {"platform": "twitter", "handle": "pepetoken"},
                {"platform": "telegram", "handle": "pepechat"},
            ],
        }
    return data


# ─── GetTokenOverviewTool ────────────────────────────────────────────


async def test_overview_picks_highest_liquidity_pair() -> None:
    mock_client = AsyncMock(spec=DexscreenerClient)
    mock_client.get_pairs_by_token.return_value = [
        _pair(addr="0xlow", liquidity_usd=1_000),
        _pair(addr="0xhigh", liquidity_usd=500_000),
        _pair(addr="0xmid", liquidity_usd=50_000),
    ]

    tool = GetTokenOverviewTool(client=mock_client)
    result = await tool.execute(chain="base", token_address="0xbase")

    assert result["found"] is True
    assert result["pair_address"] == "0xhigh"
    assert result["liquidity_usd"] == 500_000
    assert result["price_change_24h_pct"] == 42.0


async def test_overview_handles_no_pairs() -> None:
    mock_client = AsyncMock(spec=DexscreenerClient)
    mock_client.get_pairs_by_token.return_value = []

    tool = GetTokenOverviewTool(client=mock_client)
    result = await tool.execute(chain="base", token_address="0xnone")

    assert result["found"] is False
    assert "No trading pairs" in result["message"]


# ─── ScanNewPairsTool ────────────────────────────────────────────────


async def test_scan_new_pairs_filters_by_chain_and_liquidity() -> None:
    mock_client = AsyncMock(spec=DexscreenerClient)
    # Profiles include base (wanted) + solana (discarded)
    mock_client.get_latest_token_profiles.return_value = [
        {"chainId": "base", "tokenAddress": "0xA"},
        {"chainId": "solana", "tokenAddress": "solXYZ"},
        {"chainId": "base", "tokenAddress": "0xB"},
        {"chainId": "base", "tokenAddress": "0xC"},
    ]

    # 0xA has high liq (pass), 0xB has low liq (drop), 0xC has high liq (pass)
    async def pairs_by_token(chain: str, token: str) -> list[dict[str, Any]]:
        if token == "0xA":
            return [_pair(liquidity_usd=20_000, symbol="AAA")]
        if token == "0xB":
            return [_pair(liquidity_usd=500, symbol="BBB")]  # below filter
        if token == "0xC":
            return [_pair(liquidity_usd=80_000, symbol="CCC")]
        return []

    mock_client.get_pairs_by_token.side_effect = pairs_by_token

    tool = ScanNewPairsTool(client=mock_client)
    result = await tool.execute(chain="base", limit=5, min_liquidity_usd=5_000)

    assert result["count"] == 2
    symbols = [p["symbol"] for p in result["pairs"]]
    assert "AAA" in symbols
    assert "CCC" in symbols
    assert "BBB" not in symbols


async def test_scan_new_pairs_respects_limit() -> None:
    mock_client = AsyncMock(spec=DexscreenerClient)
    mock_client.get_latest_token_profiles.return_value = [
        {"chainId": "base", "tokenAddress": f"0x{i}"} for i in range(10)
    ]
    mock_client.get_pairs_by_token.side_effect = lambda *a, **k: [
        _pair(liquidity_usd=100_000)
    ]

    tool = ScanNewPairsTool(client=mock_client)
    result = await tool.execute(chain="base", limit=3, min_liquidity_usd=0)

    assert result["count"] == 3


# ─── GetTokenSocialStatsTool ─────────────────────────────────────────


async def test_socials_extracted_from_pair_info() -> None:
    mock_client = AsyncMock(spec=DexscreenerClient)
    mock_client.get_pairs_by_token.return_value = [_pair(with_info=True)]

    tool = GetTokenSocialStatsTool(client=mock_client)
    result = await tool.execute(chain="base", token_address="0xbase")

    assert result["found"] is True
    assert result["websites"] == ["https://pepe.example"]
    assert {"platform": "twitter", "handle": "pepetoken"} in result["socials"]
    assert result["has_any_socials"] is True


async def test_socials_handles_missing_info() -> None:
    mock_client = AsyncMock(spec=DexscreenerClient)
    mock_client.get_pairs_by_token.return_value = [_pair(with_info=False)]

    tool = GetTokenSocialStatsTool(client=mock_client)
    result = await tool.execute(chain="base", token_address="0xbase")

    assert result["found"] is True
    assert result["websites"] == []
    assert result["socials"] == []
    assert result["has_any_socials"] is False
