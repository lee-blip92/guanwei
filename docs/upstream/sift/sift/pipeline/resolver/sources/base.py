"""Per-source resolver abstraction.

Each resolver knows how to take a ``ProductProfile`` and produce a
``SourceRef`` for a single source. Most use the shared ``SearchClient``
to do a ``site:`` search; a few (e.g. App Store) hit the source's own
search API directly. The base class handles the common search +
extract + disambiguate flow so concrete resolvers only declare the
query template and the URL → identifier parser.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import List, Optional
from urllib.parse import urlparse

from sift.pipeline.resolver.disambiguator import LLMDisambiguator
from sift.pipeline.resolver.models import (
    ProductProfile,
    ResolverCandidate,
    SearchHit,
    SourceRef,
)
from sift.pipeline.resolver.search.base import SearchClient

logger = logging.getLogger(__name__)


class SourceResolver(ABC):
    def __init__(
        self,
        search_client: Optional[SearchClient] = None,
        disambiguator: Optional[LLMDisambiguator] = None,
    ):
        self.search_client = search_client
        self.disambiguator = disambiguator

    @property
    @abstractmethod
    def source_name(self) -> str: ...

    @abstractmethod
    def resolve(self, profile: ProductProfile) -> Optional[SourceRef]: ...

    # -- Helpers --------------------------------------------------------

    def _site_search(self, query: str, limit: int = 10) -> List[SearchHit]:
        if self.search_client is None:
            return []
        return self.search_client.web_search(query, limit=limit)

    def _disambiguate(
        self, profile: ProductProfile, candidates: List[ResolverCandidate]
    ) -> Optional[SourceRef]:
        if not candidates:
            return None
        if self.disambiguator is not None and len(candidates) > 1:
            picked = self.disambiguator.pick(profile, candidates)
        else:
            picked = max(candidates, key=lambda c: c.raw_score)
        return picked.ref if picked else None


def parse_path_segment(url: str, prefix: str) -> Optional[str]:
    """Extract the segment immediately after ``prefix`` in a URL path.

    e.g. parse_path_segment("https://g2.com/products/notion/reviews", "/products/")
         → "notion"
    """
    path = urlparse(url).path
    if prefix not in path:
        return None
    after = path.split(prefix, 1)[1]
    segment = after.split("/", 1)[0].split("?", 1)[0]
    return segment or None


def host_matches(url: str, *suffixes: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return any(host == s or host.endswith("." + s) for s in suffixes)


_SLUG_TOKEN_RE = re.compile(r"[a-z0-9]+")


def slug_token_set(value: str) -> set[str]:
    return set(_SLUG_TOKEN_RE.findall(value.lower()))


def slug_join(value: str) -> str:
    """Order-preserving concatenation of alphanumeric tokens in ``value``.

    Used to synthesise a slug-shaped fallback identifier from a product name:
    ``"Notion AI"`` → ``"notionai"`` (not ``"ainotion"`` as a sorted set would
    give).
    """
    return "".join(_SLUG_TOKEN_RE.findall(value.lower()))
