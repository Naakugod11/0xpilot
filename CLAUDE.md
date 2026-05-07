# CLAUDE.md

Context for Claude Code working on this repo.

## What this is

0xpilot — autonomous Web3 research agent. Phase 3 of a 5-phase Web3+AI roadmap (Phase 1: web3-ai-agent, Phase 2: 0xbrain RAG, Phase 3: this, Phase 4: trading bot, Phase 5: ZK integration).

Decision-support tool, not autonomous trading. User asks about a token / wallet / chain. Agent decides which on-chain tools to call, synthesizes a structured report (Data Summary → 🚩 Red Flags → Analysis → Assessment with confidence rating + DYOR disclaimer). Red flags override bullish signals (honeypot, unlocked LP, whale concentration, unverified contract = bearish even with hype).

## Stack

- Python 3.12+, **uv** package manager (PEP 735 dependency-groups in `pyproject.toml`)
- FastAPI, async httpx, raw Anthropic SDK (NO LangGraph / agent frameworks)
- structlog JSON logs, in-memory MetricsCollector with p50/p95/p99 latencies
- pytest + respx for HTTP mocking, ruff for lint
- Tailwind CDN + vanilla JS terminal UI, SSE for streaming
- Model: `claude-sonnet-4-5-20250929`

## Architecture
app/
├── agent/
│   ├── loop.py         # AgentLoop with run() + run_streaming() (raw SDK, ~150 lines)
│   ├── prompts.py      # SYSTEM_PROMPT with red-flag-override structure
│   └── schemas.py      # ChatRequest, ChatResponse, ToolCallRecord, StopReason
├── tools/
│   ├── base.py         # BaseTool ABC (name, description, input_schema, execute)
│   ├── registry.py     # ToolRegistry + build_default_registry()
│   ├── onchain.py      # GasPriceTool (Alchemy)
│   ├── market.py       # Dexscreener: overview, scan_new_pairs, social_stats
│   ├── security.py     # GoPlus + Etherscan (dual-source for holders)
│   ├── wallet.py       # Zerion: PnL + curated smart money YAML
│   ├── market_history.py  # Coingecko: OHLC + simulate_entry
│   └── knowledge.py    # ResolveEnsTool + Query0xbrainTool
├── clients/            # one async httpx client per external API
│   ├── alchemy.py      # RPC + ENS via raw eth_call + namehash
│   ├── dexscreener.py
│   ├── goplus.py
│   ├── zerion.py       # HTTP Basic auth, follow_redirects=True
│   ├── coingecko.py    # x-cg-demo-api-key header
│   ├── moralis.py    # primary holder distribution source
│   └── oxbrain.py      # HTTP client to Phase 2 RAG service
├── observability/
│   ├── logger.py       # structlog setup
│   └── metrics.py      # MetricsCollector with asyncio.Lock
└── api/
├── middleware.py   # request_context_middleware
└── routes.py       # /health /tools /metrics /chat /chat/stream
frontend/index.html     # single-file brutalist amber-mono terminal UI
data/smart_money.yaml   # curated wallet registry, public attribution only
tests/                  # ~80+ tests, hot paths covered

## Tools (12)

| Tool | Source | Purpose |
|---|---|---|
| `get_token_overview` | Dexscreener | Most-liquid pair, price/liq/FDV/24h |
| `scan_new_pairs` | Dexscreener | New pairs filtered by chain + min liquidity |
| `get_token_social_stats` | Dexscreener | Websites, socials |
| `get_token_security` | GoPlus | Honeypot, taxes, mint auth, LP lock, hidden owner |
| `get_holder_distribution` | Moralis + Alchemy cross-check | Dual-source, flags disagreement |
| `get_wallet_pnl` | Zerion | Multi-chain PnL |
| `track_smart_money` | Zerion + YAML | Trades of curated wallets |
| `get_historical_ohlc` | Coingecko | Candles + drawdown stats |
| `simulate_entry` | computed | "If I'd bought $X N days ago" |
| `get_gas_price` | Alchemy | 5 EVM chains |
| `resolve_ens` | Alchemy raw RPC | Bidirectional ENS ↔ address |
| `query_0xbrain` | Phase 2 RAG | Whitepaper retrieval |

Block 5 (Telegram + Twitter social) is parked for Phase 3.7. Solana support is Phase 3.5 via Helius.

## Parked tools

| Tool | Code | Reason parked | Target phase |
|---|---|---|---|
| `detect_fair_value_gaps` | `app/tools/technical.py`, `app/clients/bybit.py` | Bybit v5 public API returns 403 from cloud provider IPs (Railway/GCP) even with browser User-Agent. Works locally, unusable in production. | Phase 4 — revisit with self-hosted Cloudflare Worker proxy or paid TA data provider (e.g. TradingView, Tardis) |

## Chain support

First-class: **Ethereum, Base** (full tool coverage). Opportunistic via Zerion: Arbitrum, BSC, Polygon, Optimism. Solana → Phase 3.5.

## Dev workflow

```bash
uv sync
cp .env.example .env  # fill: ANTHROPIC_API_KEY, ALCHEMY_API_KEY, ZERION_API_KEY,
                      #       COINGECKO_API_KEY, ETHERSCAN_API_KEY, OXBRAIN_BASE_URL

uv run uvicorn app.main:app --reload     # dev server (UI at http://localhost:8000)
uv run pytest                            # ~80 tests
uv run ruff check .
```

Endpoints:
- `GET /` → frontend UI
- `GET /health` → liveness
- `GET /tools` → tool catalog (Anthropic schemas)
- `GET /metrics` → in-memory aggregated stats
- `POST /chat` → single-turn (sync)
- `POST /chat/stream` → SSE streaming

## Key design decisions (don't undo without asking)

- **Raw Anthropic SDK over agent frameworks.** Loop is hand-rolled to keep control flow visible. ~150 lines, multi-iteration, max_iterations + token budget guards, tool errors recover via `is_error` tool_result blocks.
- **Tool registry pattern.** All tools implement BaseTool ABC. Registry exposes Anthropic schemas via `get_anthropic_schemas()`. Adding a new tool = add file to `app/tools/` + register in `build_default_registry()`.
- **System prompt enforces structured output.** Data Summary → 🚩 Red Flags → Analysis → Assessment (overall/confidence/what-would-change-view) + DYOR. Red flags override bullish signals.
- **Observability from day 1.** Every tool call records latency. `/metrics` exposes p50/p95/p99 per tool, agent run breakdown, token usage. structlog with request-id contextvars propagation.
- **Phase 1+2+3 talk via HTTP, not merged into one repo.** 0xbrain is a separate Railway service queried as a tool. Service separation is a deliberate architectural signal.
- **Smart money YAML uses public attribution only.** Foundation wallets, DAO treasuries, ENS-attested founders. No unverified random wallet labels. Extension workflow: Nansen → Arkham → 90d verification → add with tags + source.

## Current open task

`get_holder_distribution` is being switched to a dual-source pattern after GoPlus and Alchemy `/nft/v3/getOwnersForContract` both proved unreliable for ERC-20 tokens (GoPlus returned `holder_count: 2` for tokens with 12k+ holders; Alchemy NFT API returns empty `owners: []` for ERC-20 on free tier including WETH).

**Decision: Etherscan primary + Alchemy cross-check.** Both clients called in parallel via `asyncio.gather`. Etherscan is the source of truth. Alchemy result attached as `cross_check.alchemy_holder_count`. If they disagree (e.g. Alchemy 0, Etherscan 12k), output flags `cross_check.agreement: false` with a note. Agent uses this to communicate data uncertainty to the user.

Implementation:
1. New `app/clients/etherscan.py` with `get_token_holders` using v2 multichain API (`https://api.etherscan.io/v2/api?chainid=...&module=token&action=tokenholderlist&...`)
2. Rewrite `GetHolderDistributionTool` in `app/tools/security.py` to call both, build cross-check output
3. Tool description tells the agent: "if cross_check.agreement is false, communicate the discrepancy"
4. Update tests in `tests/test_tools/test_security.py` — 5 dual-source tests mocking both clients
5. Add `ETHERSCAN_API_KEY` to `.env.example` and `app/config.py` Settings
6. Live verify against AZTEC `0xA27EC0006e59f245217Ff08CD52A7E8b169E62D2` — should return realistic ~12k holders

## Roadmap

- **Phase 3 (current):** ship deployable v1 with 12 tools, dual-source holder distribution, deployed on Railway
- **Phase 3.5:** Solana support via Helius (Pump.fun scanner as first Solana-only tool)
- **Phase 3.7:** Sentiment / hype layer — Telegram via Telethon, X via LunarCrush API
- **Phase 4:** Trading bot consuming 0xpilot research as decision layer. Vault contract for user deposits, performance fees on-chain (high-water-mark pattern), paper trading + win-rate tracking before real capital. Memory layer (`0xmemory`) = Postgres time-series + structured RAG, separate from 0xbrain.
- **Phase 5:** ZK integration via Noir circuits

## Common pitfalls in this codebase

- httpx clients need `follow_redirects=True` (Zerion API redirects `/pnl` ↔ `/pnl/`)
- Anthropic tool `input_schema` must be JSON Schema draft 2020-12 strict — `required` is a sibling of `properties`, NOT nested inside
- Module names can't start with a digit (so `0xbrain` Python module is `oxbrain.py` even though the service brand stays `0xbrain`)
- Coingecko free tier: 30rpm, 10k/month, `x-cg-demo-api-key` header (NOT bearer)
- ENS: raw `eth_call` to Registry + Public Resolver, no `ens` Python package needed
- Alchemy NFT API namespace exposes `getOwnersForContract` but returns empty for ERC-20 on free tier despite docs (confirmed via raw curl)
- Tools should keep LLM-context lean: paginate / sample large lists rather than dumping full payloads into tool_result