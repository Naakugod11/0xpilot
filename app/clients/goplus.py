"""GoPlus Security client — keyless public API for token rug indicators.

Endpoint: GET /api/v1/token_security/{chain_id}?contract_addresses={addr}

Response shape (partial — see https://docs.gopluslabs.io):
{
  "code": 1, "message": "OK",
  "result": {
    "0xTOKEN": {
      "is_honeypot": "0" | "1",
      "buy_tax": "0" | "0.05",
      "sell_tax": "0" | "0.05",
      "is_mintable": "0" | "1",
      "can_take_back_ownership": "0" | "1",
      "owner_change_balance": "0" | "1",
      "hidden_owner": "0" | "1",
      "is_open_source": "0" | "1",
      "is_proxy": "0" | "1",
      "is_blacklisted": "0" | "1",
      "is_whitelisted": "0" | "1",
      "is_anti_whale": "0" | "1",
      "cannot_buy": "0" | "1",
      "cannot_sell_all": "0" | "1",
      "trading_cooldown": "0" | "1",
      "transfer_pausable": "0" | "1",
      "slippage_modifiable": "0" | "1",
      "personal_slippage_modifiable": "0" | "1",
      "holder_count": "12345",
      "lp_holder_count": "5",
      "total_supply": "...",
      "holders": [
        {"address": "0x...", "balance": "...", "percent": "0.12",
         "is_locked": 0|1, "is_contract": 0|1, "tag": "..."},
        ...
      ],
      "lp_holders": [...]  # LP position holders (with is_locked flag)
    }
  }
}

Note: all booleans are string "0"/"1" — convert to real bool in the tool.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.observability.logger import get_logger

logger = get_logger(__name__)

Chain = Literal["ethereum", "base", "arbitrum", "bsc", "polygon", "optimism"]

# Map chain names to GoPlus's chain IDs

_CHAIN_IDS: dict[Chain, str] = {
    "ethereum": "1",
    "base": "8453",
    "arbitrum": "42161",
    "bsc": "56",
    "polygon": "137",
    "optimisim": "10",
}

BASE_URL = "https://api.gopluslabs.io/api/v1"


class GoPlusError(Exception):
    """Raised for non-retryable GoPlus failures."""

class GoPlusClient:
    """Async client for GoPlus token security API (keyless, 30req/min public tier)"""

    def __init__(self, timeout: float = 15.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _chain_id(self, chain: Chain) -> str:
        if chain not in _CHAIN_IDS:
            raise GoPlusError(f"Unsupported chain: {chain}")
        return _CHAIN_IDS[chain]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        reraise=True,
    )
    async def get_token_security(
        self, chain: Chain, token_address: str
    ) -> dict[str, Any]:
        """Fetch token security report. Returns the inner per-token dict.

        Raises GoPlusError if the API returns a non-OK code or the token
        isn't in the response (unkown contract).
        """
        chain_id = self._chain_id(chain)
        url = f"{BASE_URL}/token_security/{chain_id}"
        params = {"contract_addresses": token_address.lower()}

        logger.debug("goplus.request", url=url, params=params)
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        envelope = response.json()

        if envelope.get("code") != 1:
            raise GoPlusError(
                f"GoPlus code={envelope.get('code')} message={envelope.get('message')}"
            )

        result = envelope.get("result") or {}
        # GoPlus keys = lowercased addresses
        token_data = result.get(token_address.lower())
        if not token_data:
            raise GoPlusError(
                f"No security data returned for {token_address} on {chain}"
            )
        return token_data
