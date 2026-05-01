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

# ─── ENS resolution (raw RPC, no ens package needed) ─────────

    _ENS_REGISTRY = "0x00000000000C2E074eC69A0dFb2997BA6C7d2e1e"

    @staticmethod
    def _namehash(name: str) -> str:
        """Compute ENSIP-1 namehash. Returns 0x-prefixed hex string."""
        try:
            from eth_utils import keccak
        except ImportError as exc:
            raise AlchemyError("eth_utils not available for namehash") from exc

        node = b"\x00" * 32
        if name:
            for label in reversed(name.split(".")):
                label_hash = keccak(text=label)
                node = keccak(node + label_hash)
        return "0x" + node.hex()

    async def _eth_call(self, chain: Chain, to: str, data: str) -> str:
        """Generic eth_call wrapper. Returns hex result string."""
        return await self._rpc(
            chain,
            "eth_call",
            [{"to": to, "data": data}, "latest"],
        )

    async def resolve_ens_name_to_address(self, name: str) -> str | None:
        """ENS name → address via registry + resolver contracts. Mainnet only."""
        if not name.endswith(".eth"):
            return None

        try:
            node = self._namehash(name)
        except Exception as exc:
            logger.warning("ens.namehash.failed", name=name, error=str(exc))
            return None

        resolver_call_data = "0x0178b8bf" + node[2:]
        try:
            resolver_hex = await self._eth_call(
                "ethereum", self._ENS_REGISTRY, resolver_call_data
            )
        except Exception as exc:
            logger.warning("ens.resolver_lookup.failed", name=name, error=str(exc))
            return None

        if not resolver_hex or int(resolver_hex, 16) == 0:
            return None
        resolver_addr = "0x" + resolver_hex[-40:]

        addr_call_data = "0x3b3b57de" + node[2:]
        try:
            addr_hex = await self._eth_call("ethereum", resolver_addr, addr_call_data)
        except Exception as exc:
            logger.warning("ens.addr_lookup.failed", name=name, error=str(exc))
            return None

        if not addr_hex or int(addr_hex, 16) == 0:
            return None
        return "0x" + addr_hex[-40:]

    async def reverse_resolve_ens(self, address: str) -> str | None:
        """Address → primary ENS name. Mainnet only."""
        reverse_name = f"{address.lower().replace('0x', '')}.addr.reverse"
        try:
            node = self._namehash(reverse_name)
        except Exception as exc:
            logger.warning("ens.reverse.namehash.failed", address=address, error=str(exc))
            return None

        resolver_call_data = "0x0178b8bf" + node[2:]
        try:
            resolver_hex = await self._eth_call(
                "ethereum", self._ENS_REGISTRY, resolver_call_data
            )
        except Exception as exc:
            logger.warning("ens.reverse.resolver.failed", address=address, error=str(exc))
            return None

        if not resolver_hex or int(resolver_hex, 16) == 0:
            return None
        resolver_addr = "0x" + resolver_hex[-40:]

        name_call_data = "0x691f3431" + node[2:]
        try:
            name_hex = await self._eth_call("ethereum", resolver_addr, name_call_data)
        except Exception as exc:
            logger.warning("ens.reverse.name.failed", address=address, error=str(exc))
            return None

        if not name_hex or len(name_hex) < 130:
            return None

        try:
            length = int(name_hex[66:130], 16)
            if length == 0:
                return None
            name_bytes_hex = name_hex[130 : 130 + length * 2]
            return bytes.fromhex(name_bytes_hex).decode("utf-8")
        except Exception as exc:
            logger.warning("ens.reverse.decode.failed", address=address, error=str(exc))
            return None
