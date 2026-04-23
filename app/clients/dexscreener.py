"""Dexscreener client - keyless public API.

Rate limit: 300req/min on main endpoints. We don't enforce client-side
throttling yet...
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.observability.logger import get_logger

logger = get_logger(__name__)

# Dexscreener's chain slugs don't always match ours. Map both directions
DexChain = Literal["ethereum", "base", "arbitrum", "bsc", "polygon", "optimism", "solana"]

BASE_URL = "https://api.dexscreener.com"

class DexscreenerError(Exception):
    """Raised for non-retryable Dexscreener failures."""

class DexscreenerClient:
    """Async HTTP client for the Dexscreener public API."""

    def __init__(self, timeout: float = 10.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _get(self, path: str) -> Any:
        url = f"{BASE_URL}{path}"
        logger.debug("dexscreener.request", url=url)
        response = await self._client.get(url)
        response.raise_for_status()
        return response.json()

    async def get_pairs_by_token(self, chain: DexChain, token_address: str) -> list[dict[str, Any]]:
        """Return all trading pairs for a given token on a given chain.

        Pairs are returned in Dexscreener's order (roughly by relevance/liquidity).
        Empty list if no pairs exist.
        """
        path = f"/tokens/v1/{chain}/{token_address}"
        data = await self._get(path)
        # The endpoint returns a list of pair dicts directly (v1 schema)
        if isinstance(data, list):
            return data
        # Fallback for legacy response shapes
        return data.get("pairs", []) or []

    async def get_latest_token_profiles(self) -> list[dict[str, Any]]:
        """Return recent token profiles across all chains. Caller filters by chain."""
        data = await self._get("/token-profiles/latest/v1")
        if isinstance(data, list):
            return data
        return []
