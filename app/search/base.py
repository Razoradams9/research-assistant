"""Shared search interface.

Both Tavily and DuckDuckGo implement `SearchProvider`, so the Researcher
agent never cares which one is active — the SearchService handles the
primary/fallback logic. Each provider returns a normalized list of
`SearchResult`, so downstream code sees one consistent shape.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Literal, Optional

ProviderName = Literal["tavily", "duckduckgo"]


class SearchError(RuntimeError):
    """Raised when a provider fails (network, quota, auth, etc.)."""


@dataclass
class SearchResult:
    title: str
    url: str
    content: str  # snippet or extracted page text, already length-capped


class SearchProvider(abc.ABC):
    name: ProviderName

    @abc.abstractmethod
    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        """Return up to `max_results` results for `query`.

        Must raise SearchError on failure. Returning an empty list is a
        valid, non-error outcome (the query simply found nothing).
        """
        raise NotImplementedError
