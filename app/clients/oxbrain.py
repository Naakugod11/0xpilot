"""HTTP client for the 0xbrain RAG service.

Defual contract assumed:
    POST {base_url}/query
    body: {"query": "<question>", "top_k": <int>}
    response: {"answer": "<text>", "sources": [{"text": "...", "metadata": {...}}, ...]}

Adjust the request/response shapes if your 0xbrain endpoint differs.
"""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.observability.logger import get_logger

logger = get_logger(__name__)

class OxbrainError(Exception):
    """Raised for non retryable 0xbrain failures."""


class OxbrainClient:
    """Client for the 0xbrain RAG service."""

    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        # RAG queries can take a few seconds (embedding + retrieval + reranking)
        self._base_url = (base_url or get_settings().oxbrain_base_url).rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=6),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        reraise=True,
    )
    async def query(
            self,
            question: str,
            top_k: int = 5,
            category_filter: str | None = None,
    ) -> dict[str, Any]:
        """Send a question to 0xbrain. Returns {question, aanswer, sources}.

        category_filter: 'btc' | 'eth' | 'solana' | 'defi' | 'oracle' | 'staking'
        """
        url = f"{self._base_url}/query"
        payload: dict[str, Any] = {"question": question, "top_k": top_k}
        if category_filter:
            payload["category_filter"] = category_filter

        logger.debug("oxbrain.request", url=url, top_k=top_k, category=category_filter)
        response = await self._client.post(url, json=payload)

        if response.status_code == 404:
            raise OxbrainError(f"0xbrain endpoint not found at {url}")
        if response.status_code >= 500:
            raise OxbrainError(
                f"0xbrain server error {response.status_code}: {response.text[:200]}"
            )

        response.raise_for_status()
        return response.json()
