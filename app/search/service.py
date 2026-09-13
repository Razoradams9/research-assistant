"""Search orchestration: Tavily primary, DuckDuckGo fallback.

The Researcher agent calls `SearchService.search(query)` and gets back
results plus the name of the provider that actually served them (so the
pipeline can report when the fallback kicked in).

Fallback triggers:
- Tavily is not configured (no API key), or
- Tavily raises a SearchError (quota / 429 / auth / network), or
- Tavily returns zero results.

If both providers fail, we raise SearchError; if both return nothing,
we return an empty list (a valid "no_results" outcome, not an error).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from ..config import settings
from .base import ProviderName, SearchError, SearchResult
from .duckduckgo_provider import DuckDuckGoProvider
from .tavily_provider import TavilyProvider

logger = logging.getLogger(__name__)


@dataclass
class SearchOutcome:
    results: list[SearchResult]
    provider_used: ProviderName


class SearchService:
    def __init__(
        self,
        tavily: Optional[TavilyProvider] = None,
        duckduckgo: Optional[DuckDuckGoProvider] = None,
    ):
        self._tavily = tavily or TavilyProvider()
        self._ddg = duckduckgo or DuckDuckGoProvider()

    async def search(self, query: str, *, max_results: Optional[int] = None) -> SearchOutcome:
        n = max_results or settings.max_results_per_search

        # 1) Try Tavily if it has a key.
        if self._tavily.available:
            try:
                results = await self._tavily.search(query, max_results=n)
                if results:
                    return SearchOutcome(results=results, provider_used="tavily")
                logger.info("[search] Tavily returned 0 results, falling back to DuckDuckGo")
            except SearchError as e:
                logger.warning("[search] Tavily failed (%s), falling back to DuckDuckGo", e)
        else:
            logger.info("[search] Tavily not configured, using DuckDuckGo")

        # 2) Fallback to DuckDuckGo.
        try:
            results = await self._ddg.search(query, max_results=n)
            return SearchOutcome(results=results, provider_used="duckduckgo")
        except SearchError as e:
            logger.error("[search] DuckDuckGo also failed: %s", e)
            raise
