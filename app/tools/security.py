"""Security + holder distribution tools powered by GoPlus.

Two tools sharing one client :
- GetTokenSecurityTool: rug indicators (honeypot, liq lock, taxes, etc)
- GetHolderDistributionTool: top holders, concentration ratio, contract filter
"""

from __future__ import annotations

from typing import Any

from app.clients.goplus import Chain, GoPlusClient
from app.observability.logger import get_logger
from app.tools.base import BaseTool

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
        "Get top holders of a token with concentration metrics. Filters out "
        "LP contracts and known locker addresses (which aren't really whales). "
        "Returns top N holders, total supply held by top 10, and concentration "
        "flags. High concentration (>50% in top 10 real holders) is a rug risk."
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
                "description": "How many top holders to return (default 10, max 20).",
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["chain", "token_address"],
    }

    def __init__(self, client: GoPlusClient | None = None) -> None:
        self._client = client or GoPlusClient()

    async def execute(
        self,
        chain: Chain,
        token_address: str,
        top_n: int = 10,
        **_: Any,
    ) -> dict[str, Any]:
        data = await self._client.get_token_security(chain, token_address)
        raw_holders = data.get("holders") or []

        # Split: LP/contract holders vs. real EOA holders (the ones that matter)
        real_holders: list[dict[str, Any]] = []
        lp_or_contract_holders: list[dict[str, Any]] = []

        for h in raw_holders:
            is_contract = h.get("is_contract") == 1
            # Heuristic: LPs, bridges, lockers usually have 'tag' set or is_contract=1
            if is_contract or h.get("is_locked") == 1:
                lp_or_contract_holders.append(h)
            else:
                real_holders.append(h)

        # Format top N real holders
        def _fmt(h: dict[str, Any]) -> dict[str, Any]:
            return {
                "address": h.get("address"),
                "balance": h.get("balance"),
                "percent": round(_s2f(h.get("percent")) * 100, 3),
                "is_contract": h.get("is_contract") == 1,
                "is_locked": h.get("is_locked") == 1,
                "tag": h.get("tag") or None,
            }

        top_real = [_fmt(h) for h in real_holders[:top_n]]
        top10_concentration = sum(
            _s2f(h.get("percent")) for h in real_holders[:10]
        ) * 100

        return {
            "chain": chain,
            "token_address": token_address,
            "total_holder_count": int(_s2f(data.get("holder_count"))),
            "top_n_real_holders": top_real,
            "top10_real_concentration_pct": round(top10_concentration, 3),
            "extreme_concentration": top10_concentration > 50,
            "lp_or_contract_holders": [_fmt(h) for h in lp_or_contract_holders[:5]],
        }
