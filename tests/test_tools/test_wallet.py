"""Unit tests for Zerion-backed wallet tools."""

from __future__ import annotations

import os
from collections.abc import Generator
from unittest.mock import AsyncMock

import pytest

from app.clients.zerion import ZerionClient
from app.tools.wallet import (
    GetWalletPnlTool,
    SmartMoneyEntry,
    TrackSmartMoneyTool,
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


# ─── GetWalletPnlTool ────────────────────────────────────────────────


async def test_pnl_tool_computes_totals_and_profitability() -> None:
    mock = AsyncMock(spec=ZerionClient)
    mock.get_wallet_pnl.return_value = {
        "realized_gain": 1500.0,
        "unrealized_gain": 500.0,
        "net_invested": 10000.0,
        "total_bought": 12000.0,
        "total_sold": 2500.0,
        "total_sent": 0,
        "total_received": 0,
        "total_fee": 45.0,
    }

    tool = GetWalletPnlTool(client=mock)
    result = await tool.execute(address="0xVitalik")

    assert result["realized_gain"] == 1500.0
    assert result["unrealized_gain"] == 500.0
    assert result["total_pnl"] == 2000.0
    assert result["is_net_profitable"] is True


async def test_pnl_tool_handles_losing_wallet() -> None:
    mock = AsyncMock(spec=ZerionClient)
    mock.get_wallet_pnl.return_value = {
        "realized_gain": -800.0,
        "unrealized_gain": -200.0,
        "net_invested": 5000.0,
    }

    tool = GetWalletPnlTool(client=mock)
    result = await tool.execute(address="0xLosingWallet")

    assert result["total_pnl"] == -1000.0
    assert result["is_net_profitable"] is False


# ─── TrackSmartMoneyTool ─────────────────────────────────────────────


def _sample_entries() -> list[SmartMoneyEntry]:
    return [
        SmartMoneyEntry(
            address="0xabc",
            label="Vitalik Buterin",
            tags=("founder",),
            source="public",
            chains=("ethereum",),
        ),
        SmartMoneyEntry(
            address="0xdef",
            label="Ansem",
            tags=("meme-trader",),
            source="public X",
            chains=("base", "solana"),
        ),
    ]


async def test_smart_money_list_mode_when_no_args() -> None:
    mock = AsyncMock(spec=ZerionClient)
    tool = TrackSmartMoneyTool(client=mock, entries=_sample_entries())

    result = await tool.execute()

    assert result["mode"] == "list_available"
    assert result["count"] == 2
    labels = [w["label"] for w in result["wallets"]]
    assert "Vitalik Buterin" in labels
    assert "Ansem" in labels
    mock.get_wallet_transactions.assert_not_called()


async def test_smart_money_resolves_label_to_address() -> None:
    mock = AsyncMock(spec=ZerionClient)
    mock.get_wallet_transactions.return_value = [
        {
            "attributes": {
                "mined_at": "2026-04-20T10:00:00Z",
                "operation_type": "trade",
                "hash": "0xtxhash",
                "fee": {"value": 2.50},
                "transfers": [
                    {"direction": "out", "value": 1000, "fungible_info": {"symbol": "USDC"}},
                    {"direction": "in", "value": 0.4, "fungible_info": {"symbol": "ETH"}},
                ],
            },
            "relationships": {"chain": {"data": {"id": "ethereum"}}},
        }
    ]

    tool = TrackSmartMoneyTool(client=mock, entries=_sample_entries())
    result = await tool.execute(label="vitalik")

    assert result["mode"] == "trades"
    assert result["address"] == "0xabc"
    assert result["label"] == "Vitalik Buterin"
    assert result["count"] == 1
    assert result["trades"][0]["chain"] == "ethereum"
    assert len(result["trades"][0]["transfers"]) == 2
    mock.get_wallet_transactions.assert_awaited_once_with(
        "0xabc", limit=10, operation_types=["trade"]
    )


async def test_smart_money_accepts_explicit_address_not_in_list() -> None:
    mock = AsyncMock(spec=ZerionClient)
    mock.get_wallet_transactions.return_value = []

    tool = TrackSmartMoneyTool(client=mock, entries=_sample_entries())
    result = await tool.execute(address="0xRandomUserWallet")

    assert result["mode"] == "trades"
    assert result["address"] == "0xrandomuserwallet"
    assert result["label"] is None
    assert result["source"] == "user-supplied"


async def test_smart_money_label_not_found() -> None:
    mock = AsyncMock(spec=ZerionClient)
    tool = TrackSmartMoneyTool(client=mock, entries=_sample_entries())

    result = await tool.execute(label="nonexistent person")

    assert result["mode"] == "not_found"
    assert "nonexistent person" in result["label_searched"]
    mock.get_wallet_transactions.assert_not_called()
