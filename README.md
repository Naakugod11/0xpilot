# 0xpilot

**Autonomous Web3 research agent with tool use.** Built for degens who want real
alpha: meme coin scanning, security checks, wallet PnL, smart money tracking,
social hype signal — all callable by an LLM that decides what to fetch.

Phase 3 of a personal Web3 + AI roadmap:
`web3-ai-agent` (Phase 1) → `0xbrain` (Phase 2 RAG) → **0xpilot** (Phase 3) → trading bot (Phase 4) → ZK (Phase 5).

## Why

Most "AI + crypto agent" projects are either read-only wallet explorers or
abstract tool-use demos. 0xpilot is built around the workflow I actually want
to run before entering a position: scan new pairs, check rug indicators,
look at holder distribution, check who's aping, check the TG / X chatter,
run the numbers on a hypothetical entry.

The agent decides which of those tools to call based on the question. The
tools themselves are the research layer Phase 4 (trading bot) will consume.

## Architecture

```
User → FastAPI /chat → AgentLoop ──▶ Anthropic API (tool use)
                          │            │
                          ▼            ▼
                   ToolRegistry ◀── tool_use blocks
                          │
                          ├─ Market data  (Dexscreener, Coingecko)
                          ├─ Security     (GoPlus)
                          ├─ Wallet intel (Zerion + curated smart-money YAML)
                          ├─ On-chain     (Alchemy — holders, gas, ENS)
                          ├─ Social       (Telegram via Telethon, X via API/Nitter)
                          └─ Knowledge    (0xbrain RAG)
```

Design principles:

- **No agent framework.** The loop is hand-rolled on the raw Anthropic SDK —
  transparent control flow, no hidden state, no magic.
- **Tool registry pattern.** Each tool is a `BaseTool` subclass with JSON schema
  + `async execute()`. Adding a tool = single file change.
- **Observability from day one.** structlog JSON logs, request-id propagation,
  per-tool latency and token counters.
- **Red flags override bullish signals.** Agent output always surfaces rug
  indicators before analysis — no "this token looks great" without first
  stating "but 67% of supply is in 3 wallets".
- **Test coverage target: 80%+ on `app/agent/loop.py`.**

## Chain Strategy

- **First-class (Phase 3):** Ethereum, Base
- **Opportunistic (Phase 3):** Arbitrum, BNB Chain — supported by default
  through Zerion's unified API, not individually tested per tool
- **Phase 3.5:** Solana (Helius) — added as a separate milestone once EVM
  ships clean
- **Out of scope:** Polygon, Blast, Berachain, Bitcoin Ordinals

The focus reflects where trading activity actually concentrates in 2026:
Solana + Base dominate meme/small-cap flow; Ethereum covers mid/large caps
and blue-chip DeFi. Everything else is noise for this use case.

## Tool Surface (v1, EVM)

| Block | Tool | Source | Purpose |
|---|---|---|---|
| 1 | `get_token_overview` | Dexscreener | Price, volume, liq, FDV, 24h change |
| 1 | `scan_new_pairs` | Dexscreener | New pairs with min-liq / min-holders filter |
| 1 | `get_token_social_stats` | Dexscreener/LunarCrush | Socials, followers, links |
| 2 | `get_token_security` | GoPlus | Rug indicators: locked liq, mint auth, taxes |
| 2 | `get_holder_distribution` | Alchemy | Top N holders, concentration ratio |
| 3 | `get_wallet_pnl` | Zerion | Realized + unrealized PnL, net invested |
| 3 | `track_smart_money` | Zerion + curated YAML | Recent trades of known profitable wallets |
| 4 | `get_historical_ohlc` | Coingecko | OHLCV for simulations |
| 4 | `simulate_entry` | computed | "If I'd bought $X of token Y at time T, I'd have Z now" |
| 5 | `scan_telegram_channel` | Telethon | Last N msgs, token mention frequency |
| 5 | `scan_twitter_mentions` | X API / Nitter | Recent mentions, engagement proxy |
| 6 | `get_gas_price` | Alchemy | Current gas |
| 6 | `resolve_ens` | Alchemy | Bidirectional ENS ↔ address |
| 6 | `query_0xbrain` | 0xbrain RAG | Retrieve from the Phase 2 whitepaper knowledge base |

**x402 demo feature:** Zerion supports pay-per-call on Base (0.01 USDC).
`get_wallet_pnl` optionally routes through x402 to demonstrate autonomous
agent payment — flippable via env flag for the Dev Day demo.

## Setup

```bash
# Prereq: uv (https://docs.astral.sh/uv/)
uv sync

cp .env.example .env
# fill in API keys — see .env.example for which are required vs optional

uv run uvicorn app.main:app --reload
```

Smoke check:

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0","environment":"dev"}
```

## Development

```bash
uv run pytest              # run tests
uv run ruff check .        # lint
uv run ruff format .       # format
uv run pre-commit install  # enable git hooks
```

## Roadmap

### Phase 3 — EVM agent
- [x] Step 1 — Repo setup, FastAPI skeleton, `/health`
- [ ] Step 2 — Config, structured logging, request-id middleware
- [ ] Step 3 — Alchemy client + first tool (`get_gas_price`) + tests
- [ ] Step 4 — Tool registry pattern
- [ ] Step 5 — Agent loop skeleton (single iteration)
- [ ] Step 6 — Full agent loop (multi-iter, guards, errors)
- [ ] Step 7 — Remaining tools, by block
  - [ ] Block 1 — Dexscreener (3 tools)
  - [ ] Block 2 — Security & holders (2 tools)
  - [ ] Block 3 — Zerion wallet intel (2 tools)
  - [ ] Block 4 — Historical & simulation (2 tools)
  - [ ] Block 5 — Social (2 tools, riskier — cuttable if time is short)
  - [ ] Block 6 — Basics & knowledge (3 tools)
- [ ] Step 8 — Metrics polish
- [ ] Step 9 — Integration tests for agent loop
- [ ] Step 10 — Dockerize + Railway deploy
- [ ] Step 11 — Frontend demo
- [ ] Step 12 — Launch (README, demo GIF, X post)

### Phase 3.5 — Solana extension
- [ ] Helius client + `SolanaAdapter`
- [ ] Extend existing tools with `chain="solana"`
- [ ] Pump.fun-specific tools (`scan_pumpfun_new_launches`)

### Phase 4 — Trading bot
- [ ] Multi-agent architecture consuming 0xpilot tools as research layer
- [ ] Backtested recommendation engine with win-rate tracking

## Related

- [web3-ai-agent](https://github.com/Naakugod11/web3-ai-agent) — Phase 1
- [0xbrain](https://github.com/Naakugod11/0xbrain) — Phase 2

## License

MIT