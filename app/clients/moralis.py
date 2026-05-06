"""Moralis REST API client.

Primary source for ERC-20 token holder indexing (GetHolderDistributionTool).
Endpoint: GET /api/v2.2/erc20/{address}/owners

Returns holders sorted descending by balance with pre-computed percentage
relative to total supply — no additional eth_call needed.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.observability.logger import get_logger

logger = get_logger(__name__)

Chain = Literal["ethereum", "base", "arbitrum", "bsc", "polygon", "optimism"]

_CHAIN_SLUGS: dict[str, str] = {
    "ethereum": "eth",
    "base": "base",
    "arbitrum": "arbitrum",
    "bsc": "bsc",
    "polygon": "polygon",
    "optimism": "optimism",
}

_BASE_URL = "https://deep-index.moralis.io/api/v2.2"


class MoralisError(Exception):
    """Raised for non-retryable Moralis API failures."""


class MoralisClient:
    """Async REST client for the Moralis deep-index API."""

    def __init__(self, api_key: str | None = None, timeout: float = 10.0) -> None:
        key = api_key or get_settings().moralis_api_key
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"X-API-Key": key, "accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _chain_slug(self, chain: Chain) -> str:
        slug = _CHAIN_SLUGS.get(chain)
        if slug is None:
            raise MoralisError(f"Unsupported chain: {chain}")
        return slug

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        reraise=True,
    )
    async def get_token_holders(
        self,
        chain: Chain,
        contract_address: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Return top ERC-20 holders sorted descending by balance.

        Returns:
            {
                "holders": [
                    {
                        "owner_address": "0x...",
                        "balance_raw": "<decimal string>",
                        "percentage": <float>,   # % of total supply
                        "is_contract": <bool>,
                    },
                    ...
                ],
                "cursor": <str | None>,   # pass to next call for pagination
                "total": <int | None>,    # aggregate count if Moralis returns it
            }
        """
        params: dict[str, Any] = {
            "chain": self._chain_slug(chain),
            "limit": min(limit, 100),
        }
        if cursor:
            params["cursor"] = cursor

        logger.debug(
            "moralis.get_token_holders",
            chain=chain,
            contract=contract_address,
            limit=params["limit"],
        )
        response = await self._client.get(
            f"{_BASE_URL}/erc20/{contract_address}/owners",
            params=params,
        )

        if response.status_code == 429:
            raise httpx.TransportError("rate limited")  # triggers retry
        response.raise_for_status()

        data = response.json()
        raw = data.get("result") or []

        holders = []
        for h in raw:
            addr = (h.get("owner_address") or "").lower()
            try:
                pct = float(h.get("percentage_relative_to_total_supply") or 0)
            except (TypeError, ValueError):
                pct = 0.0
            holders.append(
                {
                    "owner_address": addr,
                    "balance_raw": str(h.get("balance") or "0"),
                    "percentage": pct,
                    "is_contract": bool(h.get("is_contract")),
                }
            )

        return {
            "holders": holders,
            "cursor": data.get("cursor"),
            "total": data.get("total"),  # present on some Moralis plans
        }
