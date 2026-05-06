"""Tests for FVG detection tool + Bybit client wiring."""

from __future__ import annotations

import os
from collections.abc import Generator
from unittest.mock import AsyncMock

import pytest

from app.clients.bybit import BybitClient
from app.tools.technical import DetectFairValueGapsTool, _detect_fvgs


@pytest.fixture(autouse=True)
def _env_vars() -> Generator[None, None, None]:
    original = dict(os.environ)
    os.environ.update(
        {
            "ANTHROPIC_API_KEY": "test",
            "ALCHEMY_API_KEY": "test",
            "ZERION_API_KEY": "test",
            "COINGECKO_API_KEY": "test",
            "MORALIS_API_KEY": "test",
            "OXBRAIN_BASE_URL": "https://oxbrain.example.com",
        }
    )
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    os.environ.clear()
    os.environ.update(original)
    get_settings.cache_clear()


def _kline(ts: int, o: float, h: float, l: float, c: float) -> list:
    """Build a minimal Binance kline (just the OHLC fields we use)."""
    return [ts, o, h, l, c, 0, 0, 0, 0, 0, 0, 0]


# ─── _detect_fvgs unit tests ─────────────────────────────────────────


def test_detect_fvgs_finds_unfilled_bullish_gap() -> None:
    klines = [
        _kline(1000, 100, 105, 95, 100),   # candle 0: high=105
        _kline(2000, 105, 115, 105, 115),  # candle 1: middle (gap-creating)
        _kline(3000, 115, 120, 110, 118),  # candle 2: low=110, prev_high=105 < 110 → bullish FVG
        _kline(4000, 118, 122, 115, 120),  # later candle, low=115 > gap_high=110 → not filled
    ]
    gaps = _detect_fvgs(klines)
    assert len(gaps) == 1
    assert gaps[0]["direction"] == "bullish"
    assert gaps[0]["range_low"] == 105
    assert gaps[0]["range_high"] == 110
    assert gaps[0]["filled"] is False


def test_detect_fvgs_marks_filled_when_later_wick_enters_range() -> None:
    klines = [
        _kline(1000, 100, 105, 95, 100),
        _kline(2000, 105, 115, 105, 115),
        _kline(3000, 115, 120, 110, 118),  # bullish FVG (105, 110)
        _kline(4000, 118, 119, 107, 109),  # low=107 enters range (105,110) → filled
    ]
    gaps = _detect_fvgs(klines)
    assert len(gaps) == 1
    assert gaps[0]["filled"] is True
    assert gaps[0]["filled_at_ms"] == 4000


def test_detect_fvgs_finds_bearish_gap() -> None:
    klines = [
        _kline(1000, 120, 125, 115, 118),  # high=125, low=115
        _kline(2000, 115, 116, 105, 108),  # middle
        _kline(3000, 108, 110, 100, 105),  # high=110 < prev_low=115 → bearish FVG (110, 115)
        _kline(4000, 105, 108, 102, 104),  # later: high=108 < gap_low=110 → not filled
    ]
    gaps = _detect_fvgs(klines)
    assert len(gaps) == 1
    assert gaps[0]["direction"] == "bearish"
    assert gaps[0]["range_low"] == 110
    assert gaps[0]["range_high"] == 115
    assert gaps[0]["filled"] is False


def test_detect_fvgs_handles_no_gap() -> None:
    klines = [
        _kline(1000, 100, 105, 95, 100),
        _kline(2000, 100, 106, 99, 102),
        _kline(3000, 102, 104, 100, 103),  # overlap → no gap
    ]
    assert _detect_fvgs(klines) == []


def test_detect_fvgs_empty_input() -> None:
    assert _detect_fvgs([]) == []
    assert _detect_fvgs([_kline(1000, 100, 105, 95, 100)]) == []  # too short


# ─── Tool integration ────────────────────────────────────────────────


async def test_tool_resolves_eth_to_ethusdt_and_returns_summary() -> None:
    mock = AsyncMock(spec=BybitClient)
    mock.get_klines.return_value = [
        _kline(1000, 100, 105, 95, 100),
        _kline(2000, 105, 115, 105, 115),
        _kline(3000, 115, 120, 110, 118),  # bullish FVG (105, 110), unfilled
    ]

    tool = DetectFairValueGapsTool(client=mock)
    result = await tool.execute(symbol="ETH", interval="15m")

    mock.get_klines.assert_awaited_once_with("ETHUSDT", "15m", limit=200)
    assert result["symbol"] == "ETHUSDT"
    assert result["fvg_count_total"] == 1
    assert result["fvg_count_unfilled"] == 1
    assert result["most_recent_unfilled_fvg"]["direction"] == "bullish"


async def test_tool_accepts_full_pair_directly() -> None:
    mock = AsyncMock(spec=BybitClient)
    mock.get_klines.return_value = []

    tool = DetectFairValueGapsTool(client=mock)
    result = await tool.execute(symbol="SOLUSDC", interval="1h", lookback=50)

    mock.get_klines.assert_awaited_once_with("SOLUSDC", "1h", limit=50)
    assert result["symbol"] == "SOLUSDC"
    assert result["fvg_count_total"] == 0
