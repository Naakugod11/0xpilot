"""Zerion API client - HTTP Basic auth with API key as username.

Base URL: https://api.zerion.io/v1/
Auth: Authorization: Basic base64(api_key:)
Free tier: 2000 req/month - plenty for demos.

Note: 202 Accepted means the wallet is being indexed on first access.
We retry a few times with short backoff before giving up.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.observability.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.zerion.io/v1"

class ZerionError(Exception):
    """Raised for non-retryable Zerion failures."""

class ZerionNotReady(Exception):
    """Raised when wallet is still being indexed (202 response).
    
    Retried automatically via tenacity.
    """

class ZerionClient:
    """Async client for Zerion v1 API."""

    def __init__(
            self,
            api_key: str | None = None,
            timeout: float = 15.0,
    ) -> None:
        self._api_key = api_key or get_settings().zerion_api_key
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def _auth_header(self) -> str:
        # HTTP Basic: api_key as username, empty password
        token = base64.b64encode(f"{self._api_key}:".encode()). decode()
        return f"Basic {token}"
    
    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(
            (httpx.TransportError, httpx.TimeoutException, ZerionNotReady)
        ),
        reraise=True,
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        logger.debug("zerion.request", url=url, params=params)

        response = await self._client.get(
            url,
            params=params,
            headers={
                "Authorization": self._auth_header,
                "accept": "application/json",
            },
        )

        if response.status_code == 202:
            # Wallet is beingg indexed; tenacity retries
            raise ZerionNotReady(f"Wallet indexing in progress for {path}")
        
        if response.status_code == 401:
            raise ZerionError("Unauthorized - check ZERION_API_KEY")
        
        if response.status_code == 404:
            raise ZerionError(f"Not found: {path}")
        
        response.raise_for_status()
        return response.json()
    
    async def get_wallet_pnl(
            self, address: str, currency: str = "usd"
    ) -> dict[str, Any]:
        """Realized + unrealized PnL, total bought/sold/received, fees, etc.
        
        Returns the 'data.attributes' dict from the JSON:API response.
        """
        path = f"/wallets/{address.lower()}/pnl"
        data = await self._get(path, params={"currency": currency})
        return (data.get("data") or {}).get("attributes") or {}
    
    async def get_wallet_portfolio(
            self, address: str, currency: str = "usd"
    ) -> dict[str, Any]:
        """Total value + per-chain breakdown."""
        path = f"/wallets/{address.lower()}/portfolio"
        data = await self._get(path, params={"currency": currency})
        return (data.get("data") or {}).get("attributes") or {}
    
    async def get_wallet_transactions(
            self,
            address: str,
            limit: int = 20,
            operation_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Recent transactions. operation_types example: ['trade', 'send'].
        
        Returns the 'data' list from the JSON:API response.
        """
        params: dict[str, Any] = {"page[size]": str(limit)}
        if operation_types:
            params["filter[operation_types]"] = ",".join(operation_types)

        path = f"/wallets/{address.lower()}/transactions"
        data = await self._get(path, params=params)
        return data.get("data") or []