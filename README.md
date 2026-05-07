# 0xpilot

Autonomous research agent for crypto. Built to filter rugs before I ape into them.

You give it a token, wallet, or question. It autonomously decides which on-chain
tools to call, gathers evidence across chains, then writes back a structured
report — red flags first, bullish factors second, an honest verdict last with
confidence rating. Red flag indicators (honeypot, unlocked LP, whale concentration,
unverified contract) override bullish signals — no "this token looks great" without
first stating "but 100% of supply is in 2 wallets".

→ **Live: [0xpilot-production.up.railway.app](https://0xpilot-production.up.railway.app/)**

Phase 3 of a personal Web3 + AI roadmap:
[`web3-ai-agent`](https://github.com/Naakugod11/web3-ai-agent) (Phase 1) →
[`0xbrain`](https://github.com/Naakugod11/0xbrain) (Phase 2 RAG) →
**0xpilot** (Phase 3) → trading bot → ZK.

## What it actually does

Ask it `is the token at 0xA27EC0006e59f245217Ff08CD52A7E8b169E62D2 on ethereum a rug?`

In ~4 seconds it pulls market data from Dexscreener, runs a security check via
GoPlus, fetches holder distribution from Moralis with an Alchemy cross-check,
checks social presence, then synthesizes:

```text
🚩 Red Flags
- Contract source code NOT verified — cannot independently audit
- No locked LP detected — liquidity can be pulled at any time
- Extreme holder concentration: 100% in top 10 wallets
- Token age <7 days with $62M market cap

Assessment
- Overall: Extremely bearish / likely scam
- Confidence: High
- This is a brand impersonation of the real Aztec Network privacy
  protocol. The on-chain setup is a classic exit scam pattern.
```

Real example. Caught a fake AZTEC impersonation token live during testing
(2 holders 50/50 split, unverified contract, no LP lock) in 2 iterations
and 4 tool calls.

## How it works
User → FastAPI /chat or /chat/stream
↓
AgentLoop ─── multi-iteration tool use ──→ Anthropic API
│
▼
ToolRegistry ──→ 12 tools across 6 chains
│
├─ Market data        Dexscreener + Coingecko
├─ Security           GoPlus (honeypot, taxes, LP lock, mint auth)
├─ Holder distribution Moralis + Alchemy cross-check (dual-source)
├─ Wallet intelligence Zerion + curated smart-money YAML
├─ On-chain            Alchemy (gas, ENS, RPC)
├─ Historical          Coingecko OHLC + entry simulation
└─ Knowledge           0xbrain RAG (queries Phase 2 service over HTTP)

No agent framework. The loop is hand-rolled on the raw Anthropic SDK so I
actually understand what's running. ~150 lines of explicit control flow,
multi-iteration with token + iteration budget guards, tool errors recover
into the conversation instead of crashing the request.

Observability from day one: structlog JSON logs with request-id propagation,
in-memory metrics collector exposing per-tool p50/p95/p99 latencies and token
usage at `/metrics`.

## Tools

| Tool | Source | Purpose |
|---|---|---|
| `get_token_overview` | Dexscreener | Most-liquid pair: price, liq, FDV, 24h change |
| `scan_new_pairs` | Dexscreener | New pairs filtered by chain + min liquidity |
| `get_token_social_stats` | Dexscreener | Websites, socials, project metadata |
| `get_token_security` | GoPlus | Honeypot, taxes, mint authority, LP lock, hidden owner |
| `get_holder_distribution` | Moralis + Alchemy | Top holders + concentration with cross-source agreement check |
| `get_wallet_pnl` | Zerion | Realized + unrealized PnL across chains |
| `track_smart_money` | Zerion + curated YAML | Recent trades of curated wallets (foundations, DAOs, attested founders) |
| `get_historical_ohlc` | Coingecko | Candles + summary stats (drawdown, range %) |
| `simulate_entry` | computed | "If I'd bought $X of Y N days ago, where would I be now" |
| `get_gas_price` | Alchemy | Live gas across 5 EVM chains |
| `resolve_ens` | Alchemy raw RPC | Bidirectional ENS ↔ address (no `ens` package dependency) |
| `query_0xbrain` | 0xbrain RAG | Whitepaper retrieval via my Phase 2 service |

## Chain support

First-class: **Ethereum, Base** (full tool coverage, primary RPCs).
Opportunistic via Zerion's unified API: Arbitrum, BSC, Polygon, Optimism.
Solana → Phase 3.5 via Helius once EVM ships clean.

The selection isn't comprehensive on purpose. Trading actually concentrates
on Solana + Base + Ethereum mainnet in 2026. Polygon and Blast are noise for
this use case. Better to support 6 chains well than 15 chains poorly.

## What's not in here yet

**Social / hype layer (Telegram + X).** Telegram via Telethon needs a
session-key setup that doesn't fit a clean public deploy without a burner
account; X free tier is useless in 2026 for any real signal. Will land as
Phase 3.7 with LunarCrush as the X data source and a properly isolated
Telegram session.

**Technical analysis (FVG / SMC patterns).** Prototyped against Bybit v5
public API but parked for v1 — Bybit's WAF returns 403 from cloud provider
IPs even with browser User-Agent, and Binance is regionally blocked from
Germany. Code stays in the repo (`app/tools/technical.py`, `app/clients/bybit.py`).
Will return in Phase 4 with a proper data path (self-hosted proxy or paid TA provider).

**Trading actions.** This is read-only by design. The agent never signs
anything, never sends a transaction. The write layer (Vault contract,
performance fee logic, emergency pause) belongs in Phase 4 where it's
actually a trading bot, not a research agent.

## Setup

```bash
# Prereq: uv (https://docs.astral.sh/uv/)
uv sync

cp .env.example .env
# fill: ANTHROPIC_API_KEY, ALCHEMY_API_KEY, ZERION_API_KEY,
#       COINGECKO_API_KEY, MORALIS_API_KEY, OXBRAIN_BASE_URL

uv run uvicorn app.main:app --reload
```

UI at `http://localhost:8000`, raw API at `/chat` and streaming at `/chat/stream`.
Tool catalog at `/tools`. Live metrics at `/metrics`.

## Development

```bash
uv run pytest              # ~95 tests, hot paths covered
uv run ruff check .        # lint
uv run ruff format .       # format
```

## Smart money registry

The `track_smart_money` tool reads `data/smart_money.yaml`. Default seed
contains only public attribution — foundations, DAO treasuries, ENS-attested
founders. No unverified random wallet labels.

To add tracked traders, the workflow documented in the file is:
1. Find a candidate on Nansen Smart Money or Arkham
2. Cross-check 90 days of their actual on-chain activity
3. Add to the YAML with tags + source

Watching 200 wallets gives noise. Watching 20 well-chosen ones gives alpha.

## Stack

- **Python 3.12+**, FastAPI, async httpx, web3 for raw `eth_call`
- **Anthropic SDK** with hand-rolled tool use loop
- **structlog** + JSON logs, in-memory metrics collector with asyncio.Lock
- **uv** for package management, **ruff** for lint/format
- **pytest** with `respx` for HTTP mocking, `pytest-asyncio`
- **Tailwind CDN** + vanilla JS for the terminal UI (no build step)
- **SSE** for streaming agent events to the frontend
- **Docker** + Railway for deployment

## Related

- [web3-ai-agent](https://github.com/Naakugod11/web3-ai-agent) — Phase 1: SIWE auth, structured AI outputs, on-chain data via web3
- [0xbrain](https://github.com/Naakugod11/0xbrain) — Phase 2: RAG on protocol whitepapers, deployed and queried by Phase 3 as a tool

## Attribution

Market data sourced via [CoinGecko](https://www.coingecko.com/).

## License

MIT

Built in public by [@naaku_builds](https://x.com/naaku_builds).