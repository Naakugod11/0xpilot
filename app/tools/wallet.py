"""Wallet intelligence tools powered by Zerion.

- GetWalletPnlTool: realized + unrealized PnL for any wallet
- TrackSmartMoneyTool: recent trades of curated profitable wallets + user input
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.clients.zerion import ZerionClient
from app.observability.logger import get_logger
from app.tools.base import BaseTool

logger = get_logger(__name__)

SMART_MONEY_YAML = Path(__file__).resolve().parents[2] / "data" / "smart_money.yaml"


@dataclass(frozen=True)
class SmartMoneyEntry:
    address: str
    label: str
    tags: tuple[str, ...]
    source: str
    chains: tuple[str, ...]


def _load_smart_money() -> list[SmartMoneyEntry]:
    """Load curated wallet list. Returns empty list if file missing."""
    if not SMART_MONEY_YAML.exists():
        logger.warning("smart_money.yaml.missing", path=str(SMART_MONEY_YAML))
        return []
    
    with SMART_MONEY_YAML.open("r") as f:
        raw = yaml.safe_load(f) or {}

    entries: list[SmartMoneyEntry] = []
    for item in raw.get("wallets", []):
        if not item.get("address"):
            continue
        entries.append(
            SmartMoneyEntry(
                address=item["address"].lower(),
                label=item.get("label", "unlabeled"),
                tags=tuple(item.get("tags", [])),
                source=item.get("source", "unknown"),
                chains=tuple(item.get("chains", [])),
            )
        )
    return entries


# Tool 1: wallet PnL

class GetWalletPnlTool(BaseTool):
    name = "get_wallet_pnl"
    description = (
        "Get the PnL (profit and loss) breakdown for any wallet address: "
        "realized gains, unrealized gains, net invested, total sent, total "
        "received, total fees. Works across all major EVM chains and Solana "
        "in a single call. Use this to assess whether a wallet is actually "
        "profitable before treating its trades as signal."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "address": {
                "type": "string",
                "description": "Wallet address (EVM 0x... or Solana base58).",
            },
            "currency": {
                "type": "string",
                "description": "Fiat currency to denominate values in (default usd).",
                "enum": ["usd", "eur", "gbp"],
            },
        },
        "required": ["address"],
    }

    def __init__(self, client: ZerionClient | None = None) -> None:
        self._client = client or ZerionClient()

    async def execute(
        self, address: str, currency: str = "usd", **_: Any
    ) -> dict[str, Any]:
        attrs = await self._client.get_wallet_pnl(address, currency=currency)

        realized = attrs.get("realized_gain") or 0
        unrealized = attrs.get("unrealized_gain") or 0
        net_invested = attrs.get("net_invested") or 0
        total_fees = attrs.get("total_fee") or 0

        # win rate indicator: positive realized + positive unrealized
        is_net_profitable = (realized + unrealized) > 0

        return {
            "address": address,
            "currency": currency,
            "realized_gain": realized,
            "unrealized_gain": unrealized,
            "total_pnl": realized + unrealized,
            "net_invested": net_invested,
            "total_bought": attrs.get("total_bought"),
            "total_sold": attrs.get("total_sold"),
            "total_sent": attrs.get("total_sent"),
            "total_received": attrs.get("total_received"),
            "total_fee": total_fees,
            "is_net_profitable": is_net_profitable,
        }
    
# Tool 2: smart money tracking

class TrackSmartMoneyTool(BaseTool):
    name = "track_smart_money"
    description = (
        "Get recent trades from curated 'smart money' wallets (known "
        "profitable/notable addresses) or a user-supplied address. Returns "
        "the last N trade transactions. Use this when the user asks 'what "
        "are the whales doing' or 'is [address] buying X'. Lists available "
        "smart money labels when called with no arguments."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": (
                    "Curated wallet label to look up (e.g. 'Vitalik Buterin'). "
                    "Case-insensitive substring match."
                ),
            },
            "address": {
                "type": "string",
                "description": (
                    "Explicit wallet address to query. Use this OR label, "
                    "not both."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max number of trades to return (default 10, max 25).",
                "minimum": 1,
                "maximum": 25,
            },
        },
    }

    def __init__(
        self,
        client: ZerionClient | None = None,
        entries: list[SmartMoneyEntry] | None = None,
    ) -> None:
        self._client = client or ZerionClient()
        self._entries = entries if entries is not None else _load_smart_money()

    def _resolve_target(
        self, label: str | None, address: str | None
    ) -> tuple[str | None, SmartMoneyEntry | None]:
        """Return (address, matched_entry_or_None). address is None if nothing found."""
        if address:
            lower = address.lower()
            match = next((e for e in self._entries if e.address == lower), None)
            return lower, match

        if label:
            ll = label.lower()
            match = next((e for e in self._entries if ll in e.label.lower()), None)
            return (match.address if match else None), match

        return None, None

    async def execute(
        self,
        label: str | None = None,
        address: str | None = None,
        limit: int = 10,
        **_: Any,
    ) -> dict[str, Any]:
        # No input → list available wallets (discovery mode)
        if not label and not address:
            return {
                "mode": "list_available",
                "count": len(self._entries),
                "wallets": [
                    {
                        "address": e.address,
                        "label": e.label,
                        "tags": list(e.tags),
                        "source": e.source,
                    }
                    for e in self._entries
                ],
            }

        target_address, matched_entry = self._resolve_target(label, address)

        if target_address is None:
            return {
                "mode": "not_found",
                "label_searched": label,
                "message": (
                    f"No curated wallet matched label '{label}'. "
                    "Call again with no args to see available labels, or pass "
                    "an explicit address."
                ),
            }

        txs = await self._client.get_wallet_transactions(
            target_address, limit=limit, operation_types=["trade"]
        )

        # Simplify the Zerion JSON:API response for the LLM
        trades: list[dict[str, Any]] = []
        for tx in txs:
            attrs = tx.get("attributes") or {}
            transfers = attrs.get("transfers") or []
            trades.append(
                {
                    "mined_at": attrs.get("mined_at"),
                    "operation_type": attrs.get("operation_type"),
                    "chain": (tx.get("relationships") or {})
                    .get("chain", {})
                    .get("data", {})
                    .get("id"),
                    "hash": attrs.get("hash"),
                    "fee": (attrs.get("fee") or {}).get("value"),
                    "transfers": [
                        {
                            "direction": t.get("direction"),
                            "value": t.get("value"),
                            "symbol": (t.get("fungible_info") or {}).get("symbol"),
                        }
                        for t in transfers
                    ],
                }
            )

        return {
            "mode": "trades",
            "address": target_address,
            "label": matched_entry.label if matched_entry else None,
            "tags": list(matched_entry.tags) if matched_entry else [],
            "source": matched_entry.source if matched_entry else "user-supplied",
            "count": len(trades),
            "trades": trades,
        }