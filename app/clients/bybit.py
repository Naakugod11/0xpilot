"""Bybit v5 public API client — spot kline data, no auth required.

Replaces Binance which is blocked on German IPs (HTTP 451, BaFin).
Bybit works from DE. Same surface as the old BinanceClient so the
FVG tool needs only an import swap.

Docs: https://bybit-exchange.github.io/docs/v5/market/kline
"""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.observability.logger import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://api.bybit.com"

# User-facing interval strings → Bybit API values
_INTERVAL_MAP: dict[str, str] = {
    "1m":  "1",
    "5m":  "5",
    "15m": "15",
    "30m": "30",
    "1h":  "60",
    "4h":  "240",
    "1d":  "D",
}


class BybitError(Exception):
    """Raised on Bybit API failures."""


class BybitClient:
    """Async client for the Bybit v5 spot market public API."""

    def __init__(self, timeout: float = 15.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

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
        """Fetch OHLCV candles in ASCENDING order (oldest first).

        Bybit returns newest-first; this method reverses before returning
        so the output matches what _detect_fvgs expects.

        Each kline: [open_time_ms, open, high, low, close, volume, turnover]
        All numeric values are parsed to their native types (int / float).
        """
        bybit_interval = _INTERVAL_MAP.get(interval)
        if bybit_interval is None:
            raise BybitError(
                f"Invalid interval '{interval}'. "
                f"Valid: {sorted(_INTERVAL_MAP)}"
            )

        params = {
            "category": "spot",
            "symbol": symbol.upper(),
            "interval": bybit_interval,
            "limit": min(limit, 1000),
        }

        logger.debug("bybit.get_klines", symbol=symbol, interval=interval, limit=limit)
        response = await self._client.get(f"{_BASE_URL}/v5/market/kline", params=params)

        if response.status_code == 400:
            raise BybitError(
                f"Bad request — probably invalid symbol '{symbol}'. "
                f"Use full pair like ETHUSDT, BTCUSDT, SOLUSDT."
            )
        response.raise_for_status()

        data = response.json()
        ret_code = data.get("retCode")
        if ret_code != 0:
            raise BybitError(f"Bybit API error {ret_code}: {data.get('retMsg')}")

        raw: list[list[str]] = data.get("result", {}).get("list", [])

        # Parse strings → numbers; Bybit gives [start_ms, open, high, low, close, vol, turnover]
        parsed = [
            [int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), float(k[6])]
            for k in raw
        ]

        # Bybit returns newest-first → reverse to ascending (oldest first)
        parsed.reverse()
        return parsed
