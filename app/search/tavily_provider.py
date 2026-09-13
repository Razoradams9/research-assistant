"""Tavily search provider (primary).

Uses the async client. Tavily returns a dict with a 'results' list; each
result has title/url/content. We cap content length to keep researcher
LLM input (and thus cost/latency) bounded.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..config import settings
from .base import SearchError, SearchProvider, SearchResult

logger = logging.getLogger(__name__)


class TavilyProvider(SearchProvider):
    name = "tavily"

    def __init__(self, api_key: Optional[str] = None, max_content_chars: Optional[int] = None):
        self._api_key = api_key or settings.tavily_api_key
        self._max_content_chars = max_content_chars or settings.max_content_chars
        self._client = None  # lazy — only build if we actually have a key

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _get_client(self):
        if self._client is None:
            if not self._api_key:
                raise SearchError("Tavily API key not configured")
            from tavily import AsyncTavilyClient

            self._client = AsyncTavilyClient(api_key=self._api_key)
        return self._client

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        client = self._get_client()
        try:
            resp = await client.search(
                query=query,
                max_results=max_results,
                search_depth="basic",  # cheapest; free-tier friendly
            )
        except Exception as e:  # noqa: BLE001 - normalize to SearchError
            raise SearchError(f"Tavily search failed: {e}") from e

        results = resp.get("results", []) if isinstance(resp, dict) else []
        out: list[SearchResult] = []
        for r in results:
            content = (r.get("content") or "")[: self._max_content_chars]
            out.append(
                SearchResult(
                    title=r.get("title", "") or "",
                    url=r.get("url", "") or "",
                    content=content,
                )
            )
        logger.info("[tavily] '%s' -> %d results", query, len(out))
        return out
