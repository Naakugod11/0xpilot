"""Unit tests for Coingecko-backed historical + simulation tools."""

from __future__ import annotations

import os
import time
from collections.abc import Generator
from unittest.mock import AsyncMock

import pytest

from app.clients.coingecko import CoingeckoClient
from app.tools.market_history import GetHistoricalOhlcTool, SimulateEntryTool


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


# ─── GetHistoricalOhlcTool ───────────────────────────────────────────


async def test_ohlc_tool_summary_stats() -> None:
    mock = AsyncMock(spec=CoingeckoClient)
    # Candles: [ts_ms, o, h, l, c]
    mock.get_ohlc.return_value = [
        [1_700_000_000_000, 100, 110, 95, 105],
        [1_700_086_400_000, 105, 120, 100, 115],
        [1_700_172_800_000, 115, 130, 110, 125],
        [1_700_259_200_000, 125, 140, 120, 135],
    ]

    tool = GetHistoricalOhlcTool(client=mock)
    result = await tool.execute(coin_id="ethereum", days=7)

    assert result["summary"]["first_open"] == 100
    assert result["summary"]["last_close"] == 135
    assert result["summary"]["period_high"] == 140
    assert result["summary"]["period_low"] == 95
    assert result["summary"]["pct_change"] == 35.0
    assert result["candles_full_count"] == 4


async def test_ohlc_tool_handles_empty() -> None:
    mock = AsyncMock(spec=CoingeckoClient)
    mock.get_ohlc.return_value = []

    tool = GetHistoricalOhlcTool(client=mock)
    result = await tool.execute(coin_id="nonexistent", days=30)

    assert result["summary"] == {"count": 0}
    assert result["sampled_candles"] == []


# ─── SimulateEntryTool ───────────────────────────────────────────────


async def test_simulate_entry_profit_scenario() -> None:
    mock = AsyncMock(spec=CoingeckoClient)
    # Old price = 2000, current price = 3000 → +50% on $1000 = $500 profit
    now_ms = int(time.time() * 1000)
    mock.get_price_at.side_effect = [
        (2000.0, now_ms - 30 * 86400 * 1000),  # entry
        (3000.0, now_ms),                        # current
    ]

    tool = SimulateEntryTool(client=mock)
    result = await tool.execute(coin_id="ethereum", investment_usd=1000, days_ago=30)

    assert result["entry_price_usd"] == 2000.0
    assert result["current_price_usd"] == 3000.0
    assert result["units_held"] == 0.5
    assert result["current_value_usd"] == 1500.0
    assert result["pnl_usd"] == 500.0
    assert result["pnl_pct"] == 50.0
    assert result["is_profitable"] is True


async def test_simulate_entry_loss_scenario() -> None:
    mock = AsyncMock(spec=CoingeckoClient)
    mock.get_price_at.side_effect = [
        (100.0, 1_700_000_000_000),
        (50.0, 1_710_000_000_000),
    ]

    tool = SimulateEntryTool(client=mock)
    result = await tool.execute(coin_id="somecoin", investment_usd=500, days_ago=60)

    assert result["pnl_usd"] == -250.0
    assert result["pnl_pct"] == -50.0
    assert result["is_profitable"] is False


async def test_simulate_entry_preserves_units_math() -> None:
    """Precision check: units * current_price should reconstruct current_value."""
    mock = AsyncMock(spec=CoingeckoClient)
    mock.get_price_at.side_effect = [
        (0.5, 1_700_000_000_000),    # entry
        (0.75, 1_710_000_000_000),   # current
    ]

    tool = SimulateEntryTool(client=mock)
    result = await tool.execute(coin_id="cheapcoin", investment_usd=100, days_ago=7)

    assert result["units_held"] == 200.0
    assert result["current_value_usd"] == 150.0
    assert result["pnl_usd"] == 50.0
