"""Unit tests for AlchemyClient. All HTTP is mocked via respx."""

from __future__ import annotations

import os
from collections.abc import Generator

import httpx
import pytest
import respx

from app.clients.alchemy import AlchemyClient, AlchemyError


@pytest.fixture(autouse=True)
def _env_vars() -> Generator[None, None, None]:
    original = dict(os.environ)
    os.environ.update(
        {
            "ANTHROPIC_API_KEY": "test",
            "ALCHEMY_API_KEY": "test-alchemy-key",
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


@pytest.fixture
async def client() -> AlchemyClient:
    c = AlchemyClient()
    yield c
    await c.aclose()


@respx.mock
async def test_get_gas_price_returns_wei_as_int(client: AlchemyClient) -> None:
    # 0x3b9aca00 = 1_000_000_000 wei = 1 gwei
    respx.post("https://eth-mainnet.g.alchemy.com/v2/test-alchemy-key").mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x3b9aca00"})
    )

    result = await client.get_gas_price("ethereum")

    assert result == 1_000_000_000


@respx.mock
async def test_get_gas_price_raises_on_rpc_error(client: AlchemyClient) -> None:
    respx.post("https://eth-mainnet.g.alchemy.com/v2/test-alchemy-key").mock(
        return_value=httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32600, "message": "invalid request"},
            },
        )
    )

    with pytest.raises(AlchemyError, match="invalid request"):
        await client.get_gas_price("ethereum")


async def test_unsupported_chain_raises(client: AlchemyClient) -> None:
    with pytest.raises(AlchemyError, match="Unsupported chain"):
        await client.get_gas_price("dogechain")  # type: ignore[arg-type]


@respx.mock
async def test_retries_on_transport_error(client: AlchemyClient) -> None:
    """Transport errors should retry and eventually succeed."""
    route = respx.post("https://base-mainnet.g.alchemy.com/v2/test-alchemy-key")
    route.side_effect = [
        httpx.ConnectError("boom"),
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x1"}),
    ]

    result = await client.get_gas_price("base")
    assert result == 1
    assert route.call_count == 2

# ─── DexscreenerClient tests ─────────────────────────────────────────


@pytest.fixture
async def dex_client():
    from app.clients.dexscreener import DexscreenerClient

    c = DexscreenerClient()
    yield c
    await c.aclose()


@respx.mock
async def test_dexscreener_get_pairs_by_token(dex_client) -> None:
    from app.clients.dexscreener import DexscreenerClient  # noqa: F401 (keep import for respx)

    respx.get("https://api.dexscreener.com/tokens/v1/base/0xtoken").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"pairAddress": "0xpair1", "liquidity": {"usd": 1000}},
                {"pairAddress": "0xpair2", "liquidity": {"usd": 5000}},
            ],
        )
    )

    pairs = await dex_client.get_pairs_by_token("base", "0xtoken")
    assert len(pairs) == 2
    assert pairs[1]["pairAddress"] == "0xpair2"


@respx.mock
async def test_dexscreener_empty_response_returns_empty_list(dex_client) -> None:
    respx.get("https://api.dexscreener.com/tokens/v1/ethereum/0xnone").mock(
        return_value=httpx.Response(200, json=[])
    )

    pairs = await dex_client.get_pairs_by_token("ethereum", "0xnone")
    assert pairs == []


@respx.mock
async def test_dexscreener_get_latest_profiles(dex_client) -> None:
    respx.get("https://api.dexscreener.com/token-profiles/latest/v1").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"chainId": "base", "tokenAddress": "0xa"},
                {"chainId": "solana", "tokenAddress": "solX"},
            ],
        )
    )

    profiles = await dex_client.get_latest_token_profiles()
    assert len(profiles) == 2
    assert profiles[0]["chainId"] == "base"
