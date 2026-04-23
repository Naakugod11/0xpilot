"""Alchemy JSON_RPC client.

Thin wrapper around Alchemy's multichain RPC endpoints. Just handles:
- URL construction per chain
- httpx with retry on transient failures
- JSON-RPC envelope parsing

Higher-level logic (fomrmatting results for LLM consumption) lives in tools/.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.observability.logger import get_logger

logger = get_logger(__name__)

# Chains we support first-class in Phase 3
Chain = Literal["ethereum", "base", "arbitrum", "optimisim", "polygon"]

_ALCHEMY_SUBDOMAINS: dict[Chain, str] = {
    "ethereum": "eth-mainnet",
    "base": "base-mainnet",
    "arbitrum": "arb-mainnet",
    "optimisim": "opt-mainnet",
    "polygon": "polygon-mainnet",
}

class AlchemyError(Exception):
    """Raised for non-retryable Alchemy failures (invalid params, auth, etc.)."""

class AlchemyClient:
    """Async JSON-RPC client for Alchemy across supported EVM chains."""

    def __init__(self, api_key: str | None = None, timeout: float = 10.0) -> None:
        self._api_key = api_key or get_settings().alchemy_api_key
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _url(self, chain: Chain) ->str:
        if chain not in _ALCHEMY_SUBDOMAINS:
            raise AlchemyError(f"Unsupported chain: {chain}")
        return f"https://{_ALCHEMY_SUBDOMAINS[chain]}.g.alchemy.com/v2/{self._api_key}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _rpc(self, chain: Chain, method: str, params: list[Any]) -> Any:
        """Send a JSON-RPC call. Retries on transport errors: raises on RPC errors."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        response = await self._client.post(self._url(chain), json=payload)
        response.raise_for_status()

        data = response.json()
        if "error" in data:
            err = data["error"]
            raise AlchemyError(f"RPC error {err.get('code')}: {err.get('message')}")
        return data["result"]

    async def get_gas_price(self, chain: Chain) -> int:
        """Return current gas price in wei for the given chain."""
        logger.debug("alchemy.get_gas_price", chain=chain)
        result = await self._rpc(chain, "eth_gasPrice", [])
        # return gas price as hex string
        return int(result, 16)
