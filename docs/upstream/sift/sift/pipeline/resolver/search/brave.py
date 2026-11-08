"""Brave Search API client.

Free tier: 2,000 queries/month, 1 query/sec. Users register their own
key at https://api.search.brave.com/. Sift never bundles a key.
"""

from __future__ import annotations

import logging
from typing import List

from sift.pipeline.http_client import CurlCffiSession
from sift.pipeline.rate_limiter import RateLimiter
from sift.pipeline.resolver.models import SearchHit
from sift.pipeline.resolver.search.base import SearchClient

logger = logging.getLogger(__name__)

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class BraveSearchClient(SearchClient):
    def __init__(self, api_key: str, max_requests_per_minute: int = 55):
        self.api_key = api_key
        self._session = CurlCffiSession()
        # Free tier is 1 req/sec; default below that with jitter for safety.
        self._rate_limiter = RateLimiter(
            max_requests_per_minute=max_requests_per_minute,
            jitter_range=(0.1, 0.3),
            max_retries=2,
        )

    @property
    def backend_name(self) -> str:
        return "brave"

    def web_search(self, query: str, limit: int = 10) -> List[SearchHit]:
        if not self.api_key:
            return []
        self._rate_limiter.wait()
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.api_key,
        }
        params = {"q": query, "count": min(20, max(1, limit))}
        resp = self._session.get(_BRAVE_ENDPOINT, headers=headers, params=params, timeout=15)
        if resp is None:
            logger.warning("Brave search connection failed for query=%r", query)
            return []
        if resp.status_code != 200:
            logger.warning("Brave search HTTP %d for query=%r", resp.status_code, query)
            return []
        try:
            data = resp.json()
        except Exception as e:
            logger.warning("Brave search returned invalid JSON: %s", e)
            return []
        web = (data or {}).get("web", {}) or {}
        results = web.get("results", []) or []
        hits: List[SearchHit] = []
        for rank, item in enumerate(results[:limit], start=1):
            url = item.get("url") or ""
            title = item.get("title") or ""
            snippet = item.get("description") or item.get("snippet") or ""
            if not url:
                continue
            hits.append(SearchHit(url=url, title=title, snippet=snippet, rank=rank))
        return hits
