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


# ─── GoPlusClient tests ──────────────────────────────────────────────


@pytest.fixture
async def goplus_client():
    from app.clients.goplus import GoPlusClient

    c = GoPlusClient()
    yield c
    await c.aclose()


@respx.mock
async def test_goplus_returns_token_data(goplus_client) -> None:
    respx.get("https://api.gopluslabs.io/api/v1/token_security/8453").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 1,
                "message": "OK",
                "result": {
                    "0xpepe": {
                        "token_name": "Pepe",
                        "is_honeypot": "0",
                        "holder_count": "100",
                    }
                },
            },
        )
    )

    data = await goplus_client.get_token_security("base", "0xPEPE")
    assert data["token_name"] == "Pepe"
    assert data["is_honeypot"] == "0"


@respx.mock
async def test_goplus_raises_on_non_ok_code(goplus_client) -> None:
    from app.clients.goplus import GoPlusError

    respx.get("https://api.gopluslabs.io/api/v1/token_security/1").mock(
        return_value=httpx.Response(
            200, json={"code": 2020, "message": "non-contract address", "result": {}}
        )
    )

    with pytest.raises(GoPlusError, match="code=2020"):
        await goplus_client.get_token_security("ethereum", "0xnotacontract")


@respx.mock
async def test_goplus_raises_when_token_missing(goplus_client) -> None:
    from app.clients.goplus import GoPlusError

    respx.get("https://api.gopluslabs.io/api/v1/token_security/1").mock(
        return_value=httpx.Response(
            200, json={"code": 1, "message": "OK", "result": {}}
        )
    )

    with pytest.raises(GoPlusError, match="No security data"):
        await goplus_client.get_token_security("ethereum", "0xunknown")

# ─── ZerionClient tests ──────────────────────────────────────────────


@pytest.fixture
async def zerion_client():
    from app.clients.zerion import ZerionClient

    c = ZerionClient(api_key="test-zerion-key")
    yield c
    await c.aclose()


@respx.mock
async def test_zerion_get_pnl_returns_attributes(zerion_client) -> None:
    respx.get("https://api.zerion.io/v1/wallets/0xabc/pnl").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "type": "pnl",
                    "attributes": {
                        "realized_gain": 100.0,
                        "unrealized_gain": 50.0,
                    },
                }
            },
        )
    )

    attrs = await zerion_client.get_wallet_pnl("0xABC")
    assert attrs["realized_gain"] == 100.0


@respx.mock
async def test_zerion_202_retries_then_succeeds(zerion_client) -> None:
    route = respx.get("https://api.zerion.io/v1/wallets/0xfresh/pnl")
    route.side_effect = [
        httpx.Response(202, text=""),
        httpx.Response(
            200,
            json={"data": {"attributes": {"realized_gain": 0}}},
        ),
    ]

    attrs = await zerion_client.get_wallet_pnl("0xfresh")
    assert attrs["realized_gain"] == 0
    assert route.call_count == 2


@respx.mock
async def test_zerion_401_raises_clean_error(zerion_client) -> None:
    from app.clients.zerion import ZerionError

    respx.get("https://api.zerion.io/v1/wallets/0xabc/pnl").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )

    with pytest.raises(ZerionError, match="Unauthorized"):
        await zerion_client.get_wallet_pnl("0xabc")

# ─── CoingeckoClient tests ───────────────────────────────────────────


@pytest.fixture
async def cg_client():
    from app.clients.coingecko import CoingeckoClient

    c = CoingeckoClient(api_key="test-cg-key")
    yield c
    await c.aclose()


@respx.mock
async def test_coingecko_get_ohlc(cg_client) -> None:
    respx.get("https://api.coingecko.com/api/v3/coins/ethereum/ohlc").mock(
        return_value=httpx.Response(
            200,
            json=[
                [1_700_000_000_000, 100, 110, 95, 105],
                [1_700_086_400_000, 105, 120, 100, 115],
            ],
        )
    )

    ohlc = await cg_client.get_ohlc("ethereum", days=7)
    assert len(ohlc) == 2
    assert ohlc[0][4] == 105  # close


@respx.mock
async def test_coingecko_rate_limit_raises(cg_client) -> None:
    from app.clients.coingecko import CoingeckoError

    respx.get("https://api.coingecko.com/api/v3/coins/bitcoin/ohlc").mock(
        return_value=httpx.Response(429, json={"status": {"error_code": 429}})
    )

    with pytest.raises(CoingeckoError, match="Rate limited"):
        await cg_client.get_ohlc("bitcoin", days=7)


@respx.mock
async def test_coingecko_get_price_at_picks_nearest(cg_client) -> None:
    target = 1_700_000_000
    respx.get("https://api.coingecko.com/api/v3/coins/ethereum/market_chart/range").mock(
        return_value=httpx.Response(
            200,
            json={
                "prices": [
                    [(target - 100) * 1000, 1900.0],
                    [(target - 5) * 1000, 2000.0],  # closest to target
                    [(target + 3600) * 1000, 2050.0],
                ],
                "market_caps": [],
                "total_volumes": [],
            },
        )
    )

    price, ts_ms = await cg_client.get_price_at("ethereum", target)
    assert price == 2000.0
    assert ts_ms == (target - 5) * 1000

# ─── OxbrainClient tests ─────────────────────────────────────────────


@pytest.fixture
async def oxbrain_client():
    from app.clients.oxbrain import OxbrainClient

    c = OxbrainClient(base_url="https://oxbrain.example.com")
    yield c
    await c.aclose()


@respx.mock
async def test_oxbrain_query_returns_json(oxbrain_client) -> None:
    respx.post("https://oxbrain.example.com/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "answer": "MEV is...",
                "sources": [{"text": "...", "metadata": {"source": "doc.pdf"}}],
            },
        )
    )

    data = await oxbrain_client.query("what is mev")
    assert data["answer"] == "MEV is..."
    assert len(data["sources"]) == 1


@respx.mock
async def test_oxbrain_404_raises(oxbrain_client) -> None:
    from app.clients.oxbrain import OxbrainError

    respx.post("https://oxbrain.example.com/query").mock(
        return_value=httpx.Response(404, text="not found")
    )

    with pytest.raises(OxbrainError, match="not found"):
        await oxbrain_client.query("test")


@respx.mock
async def test_oxbrain_500_raises(oxbrain_client) -> None:
    from app.clients.oxbrain import OxbrainError

    respx.post("https://oxbrain.example.com/query").mock(
        return_value=httpx.Response(503, text="upstream timeout")
    )

    with pytest.raises(OxbrainError, match="server error 503"):
        await oxbrain_client.query("test")
