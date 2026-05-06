"""Security + holder distribution tools.

- GetTokenSecurityTool:        GoPlus — rug indicators (honeypot, liq lock, taxes, etc)
- GetHolderDistributionTool:   Moralis (primary) + Alchemy (cross-check)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.clients.goplus import Chain, GoPlusClient
from app.observability.logger import get_logger
from app.tools.base import BaseTool

if TYPE_CHECKING:
    from app.clients.alchemy import AlchemyClient
    from app.clients.moralis import MoralisClient

logger = get_logger(__name__)

SUPPORTED_CHAINS: list[Chain] = ["ethereum", "base", "arbitrum", "bsc", "polygon", "optimism"]

def _s2b(value:Any) -> bool | None:
    """Convert GoPlus's '0' / '1' / '' string booleans to real bools."""
    if value in (None, ""):
        return None
    return value == "1"

def _s2f(value: Any, default: float = 0.0) -> float:
    """Safe string -> float conversion for tax rates, percentages, etc."""
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

# Tool 1 : token security

class GetTokenSecurityTool(BaseTool):
    name = "get_token_security"
    description = (
        "Run a comprehensive security check on a token contract: honeypot "
        "detection, buy/sell tax, mint authority, ownership renunciation, "
        "LP lock status, blacklis/whitelist flags, transfer pause capability. "
        "ALWAYS call this before recommending any new or unverified token. "
        "The boolean fields directly feed into the Red Flag output section."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "chain": {
                "type": "string",
                "enum": SUPPORTED_CHAINS,
                "description": "EVM chain the token is on.",
            },
            "token_address": {
                "type": "string",
                "description": "ERC20 contract address.",
            },
        },
        "required": ["chain", "token_address"],
    }

    def __init__(self, client: GoPlusClient | None = None) -> None:
        self._client = client or GoPlusClient()

    async def execute(
            self, chain: Chain, token_address: str, **_: Any
    ) -> dict[str, Any]:
        data = await self._client.get_token_security(chain, token_address)

        # Compute liquidity lock summary from lp_holders
        lp_holders = data.get("lp_holders") or []
        locked_lp_pct = sum(
            _s2f(h.get("percent")) for h in lp_holders if h.get("is_locked") == 1
        )
        has_locked_liquidity = locked_lp_pct > 0

        buy_tax = _s2f(data.get("buy_tax")) * 100 # GoPlus returns 0.05 for 5%
        sell_tax = _s2f(data.get("sell_tax")) * 100

        is_honeypot = _s2b(data.get("is_honeypot"))
        is_mintable = _s2b(data.get("is_mintable"))
        is_open_source = _s2b(data.get("is_open_source"))
        is_proxy = _s2b(data.get("is_proxy"))
        can_take_back_ownership = _s2b(data.get("can_take_back_ownership"))
        hidden_owner = _s2b(data.get("hidden_owner"))
        transfer_pausable = _s2b(data.get("transfer_pausable"))

        # Summarize severe red flags for the llm
        severe_red_flags: list[str] = []
        if is_honeypot:
            severe_red_flags.append("HONEYPOT detected - cannot sell after buying")
        if is_open_source is False:
            severe_red_flags.append("Contract source code NOT verified / open")
        if is_mintable:
            severe_red_flags.append("Mint authority NOT renounced - supply can be inflated")
        if hidden_owner:
            severe_red_flags.append("HIDDEN owner present - dev retains control covertly")
        if can_take_back_ownership:
            severe_red_flags.append("Ownership can be re-asssumed after renouncement")
        if transfer_pausable:
            severe_red_flags.append("Transfers can be paused by contract owner")
        if buy_tax > 10 or sell_tax > 10:
            severe_red_flags.append(
                f"Unusual tax: buy={buy_tax:.1f}%, sell={sell_tax:.1f}% (treshold 10%)"
            )
        if not has_locked_liquidity:
            severe_red_flags.append("No locked LP detected - liquidity can be pulled")

        return {
            "chain": chain,
            "token_address": token_address,
            "token_name": data.get("token_name"),
            "token_symbol": data.get("token_symbol"),
            "is_honeypot": is_honeypot,
            "is_open_source": is_open_source,
            "is_proxy": is_proxy,
            "is_mintable": is_mintable,
            "can_take_back_ownership": can_take_back_ownership,
            "hidden_owner": hidden_owner,
            "transfer_pausable": transfer_pausable,
            "buy_tax_pct": round(buy_tax, 3),
            "sell_tax_pct": round(sell_tax, 3),
            "holder_count": int(_s2f(data.get("holder_count"))),
            "lp_holder_count": int(_s2f(data.get("lp_holder_count"))),
            "locked_lp_pct": round(locked_lp_pct * 100, 3),
            "has_locked_liquidity": has_locked_liquidity,
            "severe_red_flags": severe_red_flags,
        }

# Tool 2: holder distribution

class GetHolderDistributionTool(BaseTool):
    name = "get_holder_distribution"
    description = (
        "Get top holders of an ERC-20 token. Primary data from Moralis; "
        "Alchemy is run in parallel as a cross-check. Returns top N holders "
        "by balance, aggregate holder count, and concentration metrics "
        "(top-10 % of supply). Burn / zero addresses are filtered so stats "
        "reflect REAL wallets. "
        "IMPORTANT — interpret output: "
        "if cross_check.agreement is false, explicitly communicate the "
        "discrepancy to the user (both counts, likely cause). "
        "if holder_count is null (data_unavailable_note present), do NOT "
        "fabricate numbers — tell the user holder data is unavailable and why."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "chain": {
                "type": "string",
                "enum": SUPPORTED_CHAINS,
                "description": "EVM chain the token is on.",
            },
            "token_address": {
                "type": "string",
                "description": "ERC20 contract address.",
            },
            "top_n": {
                "type": "integer",
                "description": "How many top holders to return (default 10, max 25).",
                "minimum": 1,
                "maximum": 25,
            },
        },
        "required": ["chain", "token_address"],
    }

    _NON_HOLDER_ADDRESSES: set[str] = {
        "0x0000000000000000000000000000000000000000",
        "0x000000000000000000000000000000000000dead",
    }

    def __init__(
        self,
        moralis_client: MoralisClient | None = None,
        alchemy_client: AlchemyClient | None = None,
    ) -> None:
        from app.clients.moralis import MoralisClient  # local import to avoid cycle
        from app.clients.alchemy import AlchemyClient

        self._moralis = moralis_client or MoralisClient()
        self._alchemy = alchemy_client or AlchemyClient()

    async def execute(
        self,
        chain: str,
        token_address: str,
        top_n: int = 10,
        **_: Any,
    ) -> dict[str, Any]:
        import asyncio

        moralis_result, alchemy_page = await asyncio.gather(
            self._moralis.get_token_holders(chain, token_address, limit=100),  # type: ignore[arg-type]
            self._alchemy.get_token_holders(chain, token_address),  # type: ignore[arg-type]
            return_exceptions=True,
        )

        # Alchemy cross-check: count whatever owners it returned (often 0 for ERC-20 free tier)
        if isinstance(alchemy_page, Exception):
            alchemy_count = 0
        else:
            alchemy_count = len(alchemy_page.get("owners") or [])

        # Moralis failed hard → surface unavailability, don't fabricate
        if isinstance(moralis_result, Exception):
            return {
                "chain": chain,
                "token_address": token_address,
                "holder_count": None,
                "top_n_real_holders": [],
                "top10_real_concentration_pct": None,
                "extreme_concentration": None,
                "cross_check": {"alchemy_holder_count": alchemy_count, "agreement": None},
                "data_unavailable_note": (
                    f"Moralis holder fetch failed: {moralis_result}. "
                    "Do not estimate holder counts — report this to the user."
                ),
            }

        holders: list[dict[str, Any]] = moralis_result["holders"]
        moralis_total: int | None = moralis_result.get("total")  # aggregate count if returned

        if not holders:
            return {
                "chain": chain,
                "token_address": token_address,
                "holder_count": moralis_total or 0,
                "top_n_real_holders": [],
                "top10_real_concentration_pct": 0.0,
                "extreme_concentration": False,
                "cross_check": {"alchemy_holder_count": alchemy_count, "agreement": alchemy_count == 0},
                "message": "No holders found via Moralis.",
            }

        # Filter burn / zero addresses
        real: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for h in holders:
            if h["owner_address"] in self._NON_HOLDER_ADDRESSES:
                excluded.append(h)
            else:
                real.append(h)

        top_real = real[:top_n]
        top_real_formatted = [
            {
                "address": h["owner_address"],
                "balance_raw": h["balance_raw"],
                "percent": round(h["percentage"], 4),
            }
            for h in top_real
        ]

        top10_concentration = sum(h["percentage"] for h in real[:10])

        # Use aggregate total from Moralis if available, else len of this page
        holder_count = moralis_total if moralis_total is not None else len(holders)

        # Agreement: within 10% of each other (Alchemy free tier often returns 0 for ERC-20)
        if holder_count == 0 and alchemy_count == 0:
            agreement = True
        elif holder_count == 0:
            agreement = False
        else:
            agreement = abs(holder_count - alchemy_count) / holder_count <= 0.10

        return {
            "chain": chain,
            "token_address": token_address,
            "holder_count": holder_count,
            "top_n_real_holders": top_real_formatted,
            "top10_real_concentration_pct": round(top10_concentration, 3),
            "extreme_concentration": top10_concentration > 50,
            "excluded_addresses_count": len(excluded),
            "cross_check": {
                "alchemy_holder_count": alchemy_count,
                "agreement": agreement,
            },
        }