"""Coingecko v3 API client.

Base URL: https://api.coingecko.com/api/v3/
Auth: x-cg-demo-api-key header (free tier: 30 rpm, 10k/month).

We expose two endpoints relevant for Phase 3:
- /coins/{id}/ohlc - candle data for configurable day ranges
- /coins/{id}/market_chart/range - exact prices in a time window
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.observability.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.coingecko.com/api/v3"


class CoingeckoError(Exception):
    """Raised for non-retryable Coingecko failures."""

class CoingeckoClient:
    """Async client for Coingecko v3."""

    def __init__(
            self,
            api_key: str | None = None,
            timeout: float = 15.0,
    ) -> None:
        self._api_key = api_key or get_settings().coingecko_api_key
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"x-cg-demo-api-key": self._api_key, "accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=6),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None ) -> Any:
        url =f"{BASE_URL}{path}"
        logger.debug("coingecko.request", url=url, params=params)
        response = await self._client.get(url, params=params)
        if response.status_code ==429:
            raise CoingeckoError("Rate limited - 30 rpm ceiling hit")
        if response.status_code == 404:
            raise CoingeckoError(f"Not found: {path}")
        response.raise_for_status()
        return response.json()

    async def get_ohlc(
            self, coin_id: str, days: int, vs_currency: str = "usd"
    ) ->list[list[float]]:
        """Candle data for last N days, Returns [[ts_ms, o, h, l, c], ...]."""
        path = f"/coins/{coin_id}/ohlc"
        return await self._get(
            path, params={"vs_currency": vs_currency, "days": str(days)}
        )

    async def get_market_chart_range(
            self,
            coin_id: str,
            from_ts: int,
            to_ts: int,
            vs_currency: str = "usd",
    ) -> dict[str, list[list[float]]]:
        """Prices/market_caps/volumes between from_ts and to_ts (unix seconds).

        Returns dict with keys 'prices', 'market_caps', 'total_volumes',
        each a list of [ts_ms, value] pairs.
        """
        path = f"/coins/{coin_id}/market_chart/range"
        return await self._get(
            path,
            params={
                "vs_currency": vs_currency,
                "from": str(from_ts),
                "to": str(to_ts),
            },
        )

    async def get_price_at(
            self, coin_id: str, target_ts: int, vs_currency: str = "usd"
    ) -> tuple[float, int]:
        """Return (price, actual_ts_ms) closest to target_ts (unix seconds).

        Coingecko doesn't expose a point query, so we fetch a narrow range
        around the target and pick the nearest timestamp. Range width scales
        with how far back we're asking (older data = less dense).
        """
        now_s = int(time.time())
        age_days = max(1, (now_s - target_ts) // 86400)
        # Coingecko free tier: daily granularity for >90d, hourly for 1-90d.
        # Either way, a +/- 1 day window safely captures a matching sample.
        window = 86400
        data = await self.get_market_chart_range(
            coin_id,
            from_ts=target_ts - window,
            to_ts=target_ts + window,
            vs_currency=vs_currency,
        )
        prices = data.get("prices") or []
        if not prices:
            raise CoingeckoError(
                f"No price samples near ts={target_ts} for {coin_id} (age {age_days}d)"
            )
        target_ms = target_ts * 1000
        best = min(prices, key=lambda p: abs(p[0] - target_ms))
        return float(best[1]), int(best[0])
