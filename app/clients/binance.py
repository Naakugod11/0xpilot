"""Binance public API client (no auth, no rate-limit issues for our usage).

We only use the /api/v3/klines endpoint for OHLCV data. Public, free,
1200 requests/minute — way over what 0xpilot will ever need.

Docs: https://developers.binance.com/docs/binance-spot-api-docs
"""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.observability.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.binance.com"

VALID_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}


class BinanceError(Exception):
    """Raised on Binance API failures."""


class BinanceClient:
    """Async client for Binance Spot public API."""

    def __init__(self, timeout: float = 15.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=6),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        reraise=True,
    )
    async def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 100,
    ) -> list[list[Any]]:
        """Fetch OHLCV candles. Returns raw Binance kline arrays.

        Each kline: [open_time, open, high, low, close, volume,
                     close_time, quote_volume, trades_count,
                     taker_buy_base_volume, taker_buy_quote_volume, ignore]
        """
        if interval not in VALID_INTERVALS:
            raise BinanceError(
                f"Invalid interval '{interval}'. "
                f"Valid: {sorted(VALID_INTERVALS)}"
            )

        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": min(limit, 1000),  # Binance hard cap
        }

        url = f"{BASE_URL}/api/v3/klines"
        logger.debug("binance.get_klines", symbol=symbol, interval=interval, limit=limit)
        response = await self._client.get(url, params=params)

        if response.status_code == 400:
            raise BinanceError(
                f"Bad request: probably invalid symbol '{symbol}'. "
                f"Use full pair like ETHUSDT, BTCUSDT, SOLUSDT."
            )
        response.raise_for_status()
        return response.json()
