"""Market data tools powered by Dexscreener.

Three tools:
- get_token_overview: price, volume, liquidity, FDV, 24h change
- scan_new_pairs: recently launched tokens with filters
- get_token_social_stats: websites + socials from pair metadata
"""

from __future__ import annotations

from typing import Any

from app.clients.dexscreener import DexChain, DexscreenerClient
from app.observability.logger import get_logger
from app.tools.base import BaseTool

logger = get_logger(__name__)

# Chains we actively support in Phase 3
SUPPORTED_CHAINS: list[DexChain] = ["ethereum", "base", "arbitrum", "bsc", "polygon", "optimism"]

def _select_best_pair(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the pair with highest USD liquidity — that's the one that matters
    for price and trading."""
    if not pairs:
        return None
    return max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)


class GetTokenOverviewTool(BaseTool):
    name = "get_token_overview"
    description = (
        "Get current market data for a token: price, 24h volume, liquidity, "
        "FDV, market cap, price changes over 5m/1h/6h/24h. Use this as the "
        "first check on any token the user asks about. Automatically picks "
        "the most liquid trading pair. Returns an error if the token has no "
        "tracked pairs."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "chain": {
                "type": "string",
                "enum": SUPPORTED_CHAINS,
                "description": "Which EVM chain the token is on.",
            },
            "token_address": {
                "type": "string",
                "description": "The ERC20 contract address of the token.",
            },
        },
        "required": ["chain", "token_address"],
    }

    def __init__(self, client: DexscreenerClient | None = None) -> None:
        self._client = client or DexscreenerClient()

    async def execute(
        self, chain: DexChain, token_address: str, **_: Any
    ) -> dict[str, Any]:
        pairs = await self._client.get_pairs_by_token(chain, token_address)
        pair = _select_best_pair(pairs)
        if pair is None:
            return {
                "found": False,
                "chain": chain,
                "token_address": token_address,
                "message": "No trading pairs found on Dexscreener for this token.",
            }

        liquidity = pair.get("liquidity") or {}
        price_change = pair.get("priceChange") or {}
        volume = pair.get("volume") or {}

        return {
            "found": True,
            "chain": chain,
            "pair_address": pair.get("pairAddress"),
            "dex": pair.get("dexId"),
            "base_token": pair.get("baseToken"),
            "quote_token": pair.get("quoteToken"),
            "price_usd": pair.get("priceUsd"),
            "liquidity_usd": liquidity.get("usd"),
            "volume_24h_usd": volume.get("h24"),
            "volume_1h_usd": volume.get("h1"),
            "price_change_5m_pct": price_change.get("m5"),
            "price_change_1h_pct": price_change.get("h1"),
            "price_change_24h_pct": price_change.get("h24"),
            "fdv": pair.get("fdv"),
            "market_cap": pair.get("marketCap"),
            "pair_created_at_ms": pair.get("pairCreatedAt"),
            "url": pair.get("url"),
        }


class ScanNewPairsTool(BaseTool):
    name = "scan_new_pairs"
    description = (
        "Scan recently launched tokens and filter for promising candidates. "
        "Uses Dexscreener's latest token profiles, filters by chain and "
        "optional minimum liquidity. Use this when the user asks about new "
        "launches, fresh meme coins, or wants to see what's just shipping. "
        "Results are sorted by recency."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "chain": {
                "type": "string",
                "enum": SUPPORTED_CHAINS,
                "description": "Which EVM chain to filter for.",
            },
            "limit": {
                "type": "integer",
                "description": "Max number of pairs to return (default 10, max 20).",
                "minimum": 1,
                "maximum": 20,
            },
            "min_liquidity_usd": {
                "type": "number",
                "description": (
                    "Minimum USD liquidity filter (default 5000). "
                    "Lower values surface more results but include more rugs."
                ),
                "minimum": 0,
            },
        },
        "required": ["chain"],
    }

    def __init__(self, client: DexscreenerClient | None = None) -> None:
        self._client = client or DexscreenerClient()

    async def execute(
        self,
        chain: DexChain,
        limit: int = 10,
        min_liquidity_usd: float = 5000.0,
        **_: Any,
    ) -> dict[str, Any]:
        profiles = await self._client.get_latest_token_profiles()
        # Filter profiles to target chain
        on_chain = [p for p in profiles if p.get("chainId") == chain]

        # Enrich: for each profile, fetch pair data to apply liquidity filter
        results: list[dict[str, Any]] = []
        for profile in on_chain[:limit * 3]:  # fetch a bit more, then filter down
            token_addr = profile.get("tokenAddress")
            if not token_addr:
                continue
            try:
                pairs = await self._client.get_pairs_by_token(chain, token_addr)
            except Exception:
                continue
            best = _select_best_pair(pairs)
            if not best:
                continue
            liq_usd = (best.get("liquidity") or {}).get("usd") or 0
            if liq_usd < min_liquidity_usd:
                continue
            results.append(
                {
                    "token_address": token_addr,
                    "symbol": (best.get("baseToken") or {}).get("symbol"),
                    "name": (best.get("baseToken") or {}).get("name"),
                    "price_usd": best.get("priceUsd"),
                    "liquidity_usd": liq_usd,
                    "volume_24h_usd": (best.get("volume") or {}).get("h24"),
                    "pair_created_at_ms": best.get("pairCreatedAt"),
                    "url": best.get("url"),
                }
            )
            if len(results) >= limit:
                break

        return {
            "chain": chain,
            "min_liquidity_usd_filter": min_liquidity_usd,
            "count": len(results),
            "pairs": results,
        }


class GetTokenSocialStatsTool(BaseTool):
    name = "get_token_social_stats"
    description = (
        "Get social links and metadata for a token: website URLs, Twitter, "
        "Telegram, Discord, and other socials if present. Useful to verify "
        "a token has a real project behind it and to follow community "
        "channels. Does NOT give follower counts — just what links exist."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "chain": {
                "type": "string",
                "enum": SUPPORTED_CHAINS,
                "description": "Which EVM chain the token is on.",
            },
            "token_address": {
                "type": "string",
                "description": "The ERC20 contract address of the token.",
            },
        },
        "required": ["chain", "token_address"],
    }

    def __init__(self, client: DexscreenerClient | None = None) -> None:
        self._client = client or DexscreenerClient()

    async def execute(
        self, chain: DexChain, token_address: str, **_: Any
    ) -> dict[str, Any]:
        pairs = await self._client.get_pairs_by_token(chain, token_address)
        pair = _select_best_pair(pairs)
        if pair is None:
            return {
                "found": False,
                "chain": chain,
                "token_address": token_address,
                "message": "No trading pairs found; no social metadata available.",
            }

        info = pair.get("info") or {}
        websites = [w.get("url") for w in info.get("websites", []) if w.get("url")]
        socials = [
            {"platform": s.get("platform"), "handle": s.get("handle")}
            for s in info.get("socials", [])
            if s.get("platform")
        ]

        return {
            "found": True,
            "chain": chain,
            "token_address": token_address,
            "symbol": (pair.get("baseToken") or {}).get("symbol"),
            "websites": websites,
            "socials": socials,
            "image_url": info.get("imageUrl"),
            "has_any_socials": bool(websites or socials),
        }
