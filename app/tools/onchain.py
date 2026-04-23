"""On-chain tools - thin wrappers over AlchemyClient that format for LLM."""

from __future__ import annotations

from typing import Any

from app.clients.alchemy import AlchemyClient, Chain
from app.observability.logger import get_logger
from app.tools.base import BaseTool

logger = get_logger(__name__)

# 1 gwei = 10^9 wei
WEI_PER_GWEI = 1_000_000_000

class GasPriceTool(BaseTool):
    name = "get_gas_price"
    description = (
        "Get the current gas price on an EVM chain. Use this when the user asks "
        "about transacton costs, whether gas is cheap right now, or when planning "
        "a swap/bridge. Returns gas price in gwei (human-readable unit)."
    )
    input_schema ={
        "type": "object",
        "properties": {
            "chain": {
                "type": "string",
                "enum": ["ethereum", "base", "arbitrum", "optimism", "polygon"],
                "description": "Which EVM chain to query.",
            },
        },
        "required": ["chain"],
    }

    def __init__(self, client: AlchemyClient | None = None) -> None:
        self._client = client or AlchemyClient()

    async def execute(self, chain: Chain, **_: Any) -> dict[str, Any]:
        wei = await self._client.get_gas_price(chain)
        gwei = wei / WEI_PER_GWEI

        logger.info("tool.gas_price.result", chain=chain, gwei=round(gwei, 3))

        return {
            "chain": chain,
            "gas_price_gwei": round(gwei, 3),
            "gas_price_wei": wei,
        }
