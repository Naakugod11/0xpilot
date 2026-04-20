"""Application settings loaded from environment variables.

All config is typed via pydantic-settings. Missing required vars fail fast
at startup rather than producing cryptic runtime errors.

Phase 3 = EVM first (ETH + Base first-class, Arbitrum/BSC opportunistic).
Phase 3.5 = Solana support (Helius key already scaffolded here).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings. Read once at startup, then treat as immutable."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ─── Core: Anthropic ─────────────────────────────────
    anthropic_api_key: str = Field(..., description="Anthropic API key")

    # ─── Core: EVM data ──────────────────────────────────
    alchemy_api_key: str = Field(..., description="Alchemy API key (RPC, holders, ENS, gas)")

    # ─── Wallet intel ────────────────────────────────────
    zerion_api_key: str = Field(..., description="Zerion API key (wallet PnL, transactions)")

    # ─── Market data ─────────────────────────────────────
    coingecko_api_key: str = Field(..., description="Coingecko API key (historical OHLC)")

    # ─── Social metadata (optional at runtime) ───────────
    lunarcrush_api_key: str = Field(default="", description="LunarCrush API key (optional)")

    # ─── Social scraping ─────────────────────────────────
    telegram_api_id: str = Field(default="", description="Telegram API ID from my.telegram.org")
    telegram_api_hash: str = Field(default="", description="Telegram API hash")
    telegram_session_path: str = Field(
        default="./data/telegram.session",
        description="Path to Telethon session file (NEVER commit this)",
    )
    twitter_bearer_token: str = Field(default="", description="X API v2 bearer (optional)")

    # ─── Knowledge ───────────────────────────────────────
    oxbrain_base_url: str = Field(..., description="Base URL of the 0xbrain RAG service")

    # ─── Phase 3.5 forward-compat ────────────────────────
    helius_api_key: str = Field(default="", description="Helius API key (Solana — Phase 3.5)")

    # ─── Runtime ─────────────────────────────────────────
    environment: Literal["dev", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ─── Agent loop tuning ───────────────────────────────
    anthropic_model: str = "claude-sonnet-4-5-20250929"
    agent_max_iterations: int = Field(default=10, ge=1, le=30)
    agent_max_tokens_budget: int = Field(default=50_000, ge=1_000)
    agent_request_timeout_seconds: int = Field(default=60, ge=10, le=300)

    # ─── CORS ────────────────────────────────────────────
    cors_allowed_origins: list[str] = Field(default_factory=list)

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_cors(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_api_id and self.telegram_api_hash)

    @property
    def solana_configured(self) -> bool:
        """Phase 3.5 guard. Tools that need Solana check this."""
        return bool(self.helius_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Use this everywhere; don't instantiate Settings directly."""
    return Settings()  # type: ignore[call-arg]
