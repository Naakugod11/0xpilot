"""Unit tests for GoPlus-backed security + holder tools."""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.clients.alchemy import AlchemyClient
from app.clients.goplus import GoPlusClient
from app.clients.moralis import MoralisClient
from app.tools.security import GetHolderDistributionTool, GetTokenSecurityTool


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
            "MORALIS_API_KEY": "test",
        }
    )
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    os.environ.clear()
    os.environ.update(original)
    get_settings.cache_clear()


def _security_payload(**overrides: Any) -> dict[str, Any]:
    """Build a realistic GoPlus per-token result dict."""
    base: dict[str, Any] = {
        "token_name": "Pepe",
        "token_symbol": "PEPE",
        "is_honeypot": "0",
        "is_open_source": "1",
        "is_proxy": "0",
        "is_mintable": "0",
        "can_take_back_ownership": "0",
        "hidden_owner": "0",
        "transfer_pausable": "0",
        "buy_tax": "0",
        "sell_tax": "0",
        "holder_count": "1500",
        "lp_holder_count": "3",
        "holders": [
            {"address": "0xwhale1", "balance": "1000", "percent": "0.15",
             "is_contract": 0, "is_locked": 0},
            {"address": "0xwhale2", "balance": "800", "percent": "0.10",
             "is_contract": 0, "is_locked": 0},
            {"address": "0xlp", "balance": "500", "percent": "0.08",
             "is_contract": 1, "is_locked": 0, "tag": "Uniswap V3"},
        ],
        "lp_holders": [
            {"address": "0xunicrypt", "percent": "0.7", "is_locked": 1,
             "tag": "Unicrypt"},
        ],
    }
    base.update(overrides)
    return base


# ─── GetTokenSecurityTool ────────────────────────────────────────────


async def test_security_tool_clean_token_has_no_severe_flags() -> None:
    mock = AsyncMock(spec=GoPlusClient)
    mock.get_token_security.return_value = _security_payload()

    tool = GetTokenSecurityTool(client=mock)
    result = await tool.execute(chain="ethereum", token_address="0xabc")

    assert result["is_honeypot"] is False
    assert result["is_open_source"] is True
    assert result["has_locked_liquidity"] is True
    assert result["severe_red_flags"] == []


async def test_security_tool_flags_honeypot_and_mintable() -> None:
    mock = AsyncMock(spec=GoPlusClient)
    mock.get_token_security.return_value = _security_payload(
        is_honeypot="1",
        is_mintable="1",
        hidden_owner="1",
        lp_holders=[],  # no locked LP
    )

    tool = GetTokenSecurityTool(client=mock)
    result = await tool.execute(chain="base", token_address="0xrug")

    flags = result["severe_red_flags"]
    assert any("HONEYPOT" in f for f in flags)
    assert any("Mint authority" in f for f in flags)
    assert any("HIDDEN owner" in f for f in flags)
    assert any("No locked LP" in f for f in flags)
    assert result["has_locked_liquidity"] is False


async def test_security_tool_flags_high_taxes() -> None:
    mock = AsyncMock(spec=GoPlusClient)
    mock.get_token_security.return_value = _security_payload(
        buy_tax="0.15",  # 15%
        sell_tax="0.20",  # 20%
    )

    tool = GetTokenSecurityTool(client=mock)
    result = await tool.execute(chain="ethereum", token_address="0xhightax")

    assert result["buy_tax_pct"] == 15.0
    assert result["sell_tax_pct"] == 20.0
    assert any("Unusual tax" in f for f in result["severe_red_flags"])


async def test_security_tool_normalizes_open_source_and_proxy() -> None:
    mock = AsyncMock(spec=GoPlusClient)
    mock.get_token_security.return_value = _security_payload(
        is_open_source="0",
    )

    tool = GetTokenSecurityTool(client=mock)
    result = await tool.execute(chain="ethereum", token_address="0xunverified")

    assert result["is_open_source"] is False
    assert any("NOT verified" in f for f in result["severe_red_flags"])


# ─── GetHolderDistributionTool (Moralis primary + Alchemy cross-check) ──


def _moralis_response(
    holders: list[tuple[str, float]],  # (address, percentage)
    total: int | None = None,
    cursor: str | None = None,
) -> dict:
    """Build a Moralis get_token_holders return value (already parsed by client)."""
    return {
        "holders": [
            {
                "owner_address": addr,
                "balance_raw": "1000000000000000000",
                "percentage": pct,
                "is_contract": False,
            }
            for addr, pct in holders
        ],
        "cursor": cursor,
        "total": total,
    }


def _alchemy_response(owner_addresses: list[str]) -> dict:
    """Build a minimal Alchemy getOwnersForContract return value."""
    return {
        "owners": [{"ownerAddress": a, "tokenBalances": [{"balance": "0x1"}]} for a in owner_addresses],
        "pageKey": None,
    }


async def test_holder_distribution_happy_path_sources_agree() -> None:
    moralis_mock = AsyncMock(spec=MoralisClient)
    alchemy_mock = AsyncMock(spec=AlchemyClient)

    moralis_mock.get_token_holders.return_value = _moralis_response(
        [("0xwhale", 10.0), ("0xmid", 5.0), ("0xsmall", 0.1)],
        total=150,
    )
    # Alchemy returns 148 owners — within 10% of Moralis's 150
    alchemy_mock.get_token_holders.return_value = _alchemy_response(
        [f"0x{i:040x}" for i in range(148)]
    )

    tool = GetHolderDistributionTool(moralis_client=moralis_mock, alchemy_client=alchemy_mock)
    result = await tool.execute(chain="ethereum", token_address="0xtoken", top_n=10)

    assert result["holder_count"] == 150
    assert len(result["top_n_real_holders"]) == 3
    assert result["top_n_real_holders"][0]["address"] == "0xwhale"
    assert result["top_n_real_holders"][0]["percent"] == 10.0
    assert result["extreme_concentration"] is False
    assert result["cross_check"]["agreement"] is True


async def test_holder_distribution_flags_extreme_concentration() -> None:
    moralis_mock = AsyncMock(spec=MoralisClient)
    alchemy_mock = AsyncMock(spec=AlchemyClient)

    moralis_mock.get_token_holders.return_value = _moralis_response(
        [("0xwhale1", 50.0), ("0xwhale2", 50.0)],
        total=2,
    )
    alchemy_mock.get_token_holders.return_value = _alchemy_response(["0xwhale1", "0xwhale2"])

    tool = GetHolderDistributionTool(moralis_client=moralis_mock, alchemy_client=alchemy_mock)
    result = await tool.execute(chain="ethereum", token_address="0xrug")

    assert result["top10_real_concentration_pct"] == pytest.approx(100.0, abs=0.001)
    assert result["extreme_concentration"] is True


async def test_holder_distribution_excludes_burn_address() -> None:
    moralis_mock = AsyncMock(spec=MoralisClient)
    alchemy_mock = AsyncMock(spec=AlchemyClient)

    moralis_mock.get_token_holders.return_value = _moralis_response(
        [
            ("0x000000000000000000000000000000000000dead", 50.0),
            ("0xrealwhale", 10.0),
            ("0xother", 5.0),
        ],
        total=3,
    )
    alchemy_mock.get_token_holders.return_value = _alchemy_response([])

    tool = GetHolderDistributionTool(moralis_client=moralis_mock, alchemy_client=alchemy_mock)
    result = await tool.execute(chain="ethereum", token_address="0xtoken")

    addrs = [h["address"] for h in result["top_n_real_holders"]]
    assert "0x000000000000000000000000000000000000dead" not in addrs
    assert "0xrealwhale" in addrs
    assert result["excluded_addresses_count"] == 1


async def test_holder_distribution_handles_empty() -> None:
    moralis_mock = AsyncMock(spec=MoralisClient)
    alchemy_mock = AsyncMock(spec=AlchemyClient)

    moralis_mock.get_token_holders.return_value = _moralis_response([], total=0)
    alchemy_mock.get_token_holders.return_value = _alchemy_response([])

    tool = GetHolderDistributionTool(moralis_client=moralis_mock, alchemy_client=alchemy_mock)
    result = await tool.execute(chain="ethereum", token_address="0xtoken")

    assert result["holder_count"] == 0
    assert result["top_n_real_holders"] == []
    assert "No holders" in result["message"]
    assert result["cross_check"]["agreement"] is True


async def test_holder_distribution_cross_check_disagrees_when_sources_differ() -> None:
    """Alchemy returns 0 (ERC-20 free-tier limit), Moralis returns 12k — agreement=False."""
    moralis_mock = AsyncMock(spec=MoralisClient)
    alchemy_mock = AsyncMock(spec=AlchemyClient)

    moralis_mock.get_token_holders.return_value = _moralis_response(
        [(f"0x{i:040x}", 0.01) for i in range(1, 11)],
        total=12_000,
    )
    alchemy_mock.get_token_holders.return_value = _alchemy_response([])  # 0 owners

    tool = GetHolderDistributionTool(moralis_client=moralis_mock, alchemy_client=alchemy_mock)
    result = await tool.execute(chain="ethereum", token_address="0xpopular")

    assert result["holder_count"] == 12_000
    assert result["cross_check"]["alchemy_holder_count"] == 0
    assert result["cross_check"]["agreement"] is False