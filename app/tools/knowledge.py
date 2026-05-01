"""ENS resolution + 0xbrain RAG knowledge tools."""

from __future__ import annotations

from typing import Any

from app.clients.alchemy import AlchemyClient
from app.clients.oxbrain import OxbrainClient
from app.observability.logger import get_logger
from app.tools.base import BaseTool

logger = get_logger(__name__)

# Tool 1: ENS resolution

class ResolveEnsTool(BaseTool):
    name = "resolve_ens"
    description = (
        "Resolve ENS names to addresses or addresses to their primary ENS name. "
        "Bidirectional: pass either 'name' (e.g. 'vitalik.eth') OR 'address' "
        "(0x...). Use this whenever the user mentions an ENS-like name to "
        "convert it to an address before passing to other tools, or to enrich "
        "an address with a human-readable label."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "ENS name to resolve forward (e.g. 'vitalik.eth').",
            },
            "address": {
                "type": "string",
                "description": "Address to reverse-resolve to a primary ENS name.",
            },
        },
    }

    def __init__(self, client: AlchemyClient | None = None) -> None:
        self._client = client or AlchemyClient()

    async def execute(
            self,
            name: str | None = None,
            address: str | None = None,
            **_: Any,
    ) -> dict[str, Any]:
        if not name and not address:
            return {
                "error": "Provide either 'name' or 'address'",
                "found": False,
            }

        if name:
            resolved = await self._client.resolve_ens_name_to_address(name)
            return {
                "found": resolved is not None,
                "direction": "forward",
                "input_name": name,
                "address": resolved,
            }

        # address path
        primary_name = await self._client.reverse_resolve_ens(address) # type: ignore[arg-type]
        return {
            "found": primary_name is not None,
            "direction": "reverse",
            "input_address": address,
            "primary_name": primary_name,
        }


# Tool 2: 0xbrain RAG

class Query0xbrainTool(BaseTool):
    name = "query_0xbrain"
    description = (
        "Query the 0xbrain knowledge base — a RAG system indexed on crypto "
        "protocol whitepapers and documentation. Use this for conceptual / "
        "educational questions about how protocols work: 'what is Uniswap V4 "
        "concentrated liquidity', 'how does EigenLayer restaking work', "
        "'explain MEV-Boost'. NOT for live data — use other tools for prices, "
        "balances, and on-chain state. Optionally narrow with category_filter "
        "for more focused results. Returns answer + scored source excerpts."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Natural language question about a crypto protocol or concept.",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of source chunks to retrieve (default 5, max 10).",
                "minimum": 1,
                "maximum": 10,
            },
            "category_filter": {
                "type": "string",
                "description": (
                    "Narrow the knowledge base by topic. Use when the question "
                    "is clearly about one area."
                ),
                "enum": ["btc", "eth", "solana", "defi", "oracle", "staking"],
            },
        },
        "required": ["question"],
    }

    def __init__(self, client: OxbrainClient | None = None) -> None:
        self._client = client or OxbrainClient()

    async def execute(
        self,
        question: str,
        top_k: int = 5,
        category_filter: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        data = await self._client.query(
            question, top_k=top_k, category_filter=category_filter
        )

        sources_compact: list[dict[str, Any]] = []
        for src in (data.get("sources") or [])[:top_k]:
            sources_compact.append(
                {
                    "title": src.get("title"),
                    "content_snippet": src.get("content_snippet"),
                    "relevance_score": src.get("relevance_score"),
                }
            )

        return {
            "question": data.get("question", question),
            "answer": data.get("answer", ""),
            "sources_count": len(sources_compact),
            "sources": sources_compact,
            "category_filter_applied": category_filter,
        }
