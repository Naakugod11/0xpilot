"""Historical + simulation tools powered by Coingecko.

- GetHistoricalOhlcTool: candle data for a token over the last N days
- SimulateEntryTool: 'if I'd bought $X of Z Z days ago, where would I be now?'
"""

from __future__ import annotations

import time
from typing import Any

from app.clients.coingecko import CoingeckoClient
from app.observability.logger import get_logger
from app.tools.base import BaseTool

logger = get_logger(__name__)

def _summarize_ohlc(ohlc: list[list[float]]) -> dict[str, Any]:
    """Compact stats from raw candles: first/last/high/low + % change."""
    if not ohlc:
        return {"count": 0}

    first_open = ohlc[0][1]
    last_close = ohlc[-1][4]
    highest = max(c[2] for c in ohlc)
    lowest = min(c[3] for c in ohlc)
    pct_change = ((last_close - first_open) / first_open) * 100 if first_open else 0

    return {
        "count": len(ohlc),
        "first_open": first_open,
        "last_close": last_close,
        "period_high": highest,
        "period_low": lowest,
        "pct_change": round(pct_change, 3),
        "drawdown_from_high_pct": (
            round(((last_close - highest) / highest) * 100, 3) if highest else 0
        ),
    }

# Tool 1: historcal OHLC

class GetHistoricalOhlcTool(BaseTool):
    name = "get_historical_ohlc"
    description = (
        "Get historical candle data (OHLC) for a cryptocurrency over a "
        "configurable period. Returns raw candles plus summary stats: "
        "period high/low, percent change, drawdown from high. Use this when "
        "the user asks about price trends, volatility, or 'how has X been doing'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "coin_id": {
                "type": "string",
                "description": (
                    "Coingecko coin ID (e.g. 'bitcoin', 'ethereum', 'solana'). "
                    "NOT the ticker — if in doubt use the full name."
                ),
            },
            "days": {
                "type": "integer",
                "description": "Lookback window in days. 1, 7, 14, 30, 90, 180, 365, or 'max'-ish.",
                "enum": [1, 7, 14, 30, 90, 180, 365],
            },
            "vs_currency": {
                "type": "string",
                "description": "Quote currency (default usd).",
                "enum": ["usd", "eur", "gbp"],
            },
        },
        "required": ["coin_id", "days"],
    }

    def __init__(self, client: CoingeckoClient | None = None) -> None:
        self._client = client or CoingeckoClient()

    async def execute(
        self,
        coin_id: str,
        days: int,
        vs_currency: str = "usd",
        **_: Any,
    ) -> dict[str, Any]:
        ohlc = await self._client.get_ohlc(coin_id, days, vs_currency)
        summary = _summarize_ohlc(ohlc)

        # Don't dump the full candle list into the LLM context — too heavy.
        # Give first / last / sampled middle entries instead.
        sampled = ohlc[:: max(1, len(ohlc) // 10)] if ohlc else []

        return {
            "coin_id": coin_id,
            "days": days,
            "vs_currency": vs_currency,
            "summary": summary,
            "sampled_candles": sampled,  # [ts_ms, o, h, l, c]
            "candles_full_count": len(ohlc),
        }

# Tool 2: simulate entry

class SimulateEntryTool(BaseTool):
    name = "simulate_entry"
    description = (
        "Simulate a hypothetical buy: 'if I had invested $X in token Y, D days "
        "ago, where would I be today?' Returns entry price, current price, "
        "units held, current USD value, absolute and percent PnL. Great for "
        "answering 'what if' questions and historical what-should-I-have-done "
        "analysis. Uses Coingecko daily prices — don't expect intra-day precision."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "coin_id": {
                "type": "string",
                "description": "Coingecko coin ID (e.g. 'bitcoin', 'ethereum').",
            },
            "investment_usd": {
                "type": "number",
                "description": "USD amount hypothetically invested.",
                "minimum": 1,
            },
            "days_ago": {
                "type": "integer",
                "description": "How many days back the hypothetical entry happened.",
                "minimum": 1,
                "maximum": 1825,  # 5 years
            },
        },
        "required": ["coin_id", "investment_usd", "days_ago"],
    }

    def __init__(self, client: CoingeckoClient | None = None) -> None:
        self._client = client or CoingeckoClient()

    async def execute(
        self,
        coin_id: str,
        investment_usd: float,
        days_ago: int,
        **_: Any,
    ) -> dict[str, Any]:
        now_s = int(time.time())
        entry_ts = now_s - (days_ago * 86400)

        entry_price, entry_ts_actual_ms = await self._client.get_price_at(
            coin_id, entry_ts
        )
        # Current price = second call with target=now, window captures latest sample
        current_price, current_ts_actual_ms = await self._client.get_price_at(
            coin_id, now_s
        )

        units = investment_usd / entry_price if entry_price else 0
        current_value = units * current_price
        pnl_usd = current_value - investment_usd
        pnl_pct = ((current_value - investment_usd) / investment_usd) * 100

        return {
            "coin_id": coin_id,
            "investment_usd": investment_usd,
            "days_ago": days_ago,
            "entry_price_usd": round(entry_price, 8),
            "entry_ts_ms": entry_ts_actual_ms,
            "current_price_usd": round(current_price, 8),
            "current_ts_ms": current_ts_actual_ms,
            "units_held": round(units, 8),
            "current_value_usd": round(current_value, 2),
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct, 3),
            "is_profitable": pnl_usd > 0,
        }
