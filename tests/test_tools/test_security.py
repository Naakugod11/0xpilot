"""Unit tests for GoPlus-backed security + holder tools."""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.clients.goplus import GoPlusClient
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


# ─── GetHolderDistributionTool ───────────────────────────────────────


async def test_holder_distribution_filters_contracts_and_lockers() -> None:
    mock = AsyncMock(spec=GoPlusClient)
    mock.get_token_security.return_value = _security_payload()

    tool = GetHolderDistributionTool(client=mock)
    result = await tool.execute(chain="ethereum", token_address="0xabc", top_n=10)

    # Only the 2 real EOA holders should show up in top_n_real_holders
    assert len(result["top_n_real_holders"]) == 2
    addrs = [h["address"] for h in result["top_n_real_holders"]]
    assert "0xwhale1" in addrs
    assert "0xlp" not in addrs  # is_contract=1 filtered out


async def test_holder_distribution_flags_extreme_concentration() -> None:
    mock = AsyncMock(spec=GoPlusClient)
    mock.get_token_security.return_value = _security_payload(
        holders=[
            {"address": f"0x{i}", "balance": "1", "percent": "0.07",
             "is_contract": 0, "is_locked": 0}
            for i in range(10)
        ],  # 10 holders * 7% = 70% total
    )

    tool = GetHolderDistributionTool(client=mock)
    result = await tool.execute(chain="ethereum", token_address="0xwhale")

    assert result["top10_real_concentration_pct"] == pytest.approx(70.0, rel=1e-3)
    assert result["extreme_concentration"] is True


async def test_holder_distribution_respects_top_n() -> None:
    mock = AsyncMock(spec=GoPlusClient)
    mock.get_token_security.return_value = _security_payload(
        holders=[
            {"address": f"0x{i}", "balance": "1", "percent": "0.02",
             "is_contract": 0, "is_locked": 0}
            for i in range(15)
        ],
    )

    tool = GetHolderDistributionTool(client=mock)
    result = await tool.execute(chain="ethereum", token_address="0xabc", top_n=5)

    assert len(result["top_n_real_holders"]) == 5
