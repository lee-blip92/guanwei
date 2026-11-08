"""Search backend abstraction.

Per-source resolvers depend on this interface, not on Brave specifically,
so we can swap backends (SerpAPI, Tavily, sitemap-only) without touching
every resolver.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from sift.pipeline.resolver.models import SearchHit


class SearchClient(ABC):
    @property
    @abstractmethod
    def backend_name(self) -> str: ...

    @abstractmethod
    def web_search(self, query: str, limit: int = 10) -> List[SearchHit]:
        """Return ranked organic results for ``query``. Empty list on failure."""
