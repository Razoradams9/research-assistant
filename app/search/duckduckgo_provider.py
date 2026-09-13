"""DuckDuckGo search provider (fallback).

No API key required — used when Tavily is unconfigured, rate-limited, or
returns nothing. The `ddgs` library is synchronous, so we run it in a
thread via asyncio.to_thread to avoid blocking the event loop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ..config import settings
from .base import SearchError, SearchProvider, SearchResult

logger = logging.getLogger(__name__)


class DuckDuckGoProvider(SearchProvider):
    name = "duckduckgo"

    def __init__(self, max_content_chars: Optional[int] = None):
        self._max_content_chars = max_content_chars or settings.max_content_chars

    @property
    def available(self) -> bool:
        return True  # no key needed, always available

    def _search_sync(self, query: str, max_results: int) -> list[dict]:
        from ddgs import DDGS

        return DDGS().text(query, max_results=max_results)

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        try:
            raw = await asyncio.to_thread(self._search_sync, query, max_results)
        except Exception as e:  # noqa: BLE001 - normalize to SearchError
            raise SearchError(f"DuckDuckGo search failed: {e}") from e

        out: list[SearchResult] = []
        for r in raw or []:
            content = (r.get("body") or "")[: self._max_content_chars]
            out.append(
                SearchResult(
                    title=r.get("title", "") or "",
                    url=r.get("href", "") or "",
                    content=content,
                )
            )
        logger.info("[duckduckgo] '%s' -> %d results", query, len(out))
        return out
