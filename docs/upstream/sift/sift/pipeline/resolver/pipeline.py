"""Top-level resolver orchestrator.

Glues the cache, enricher, per-source resolvers, and sitemap fallback
into a single ``resolve(name, sources)`` call. Designed to be safe to
call repeatedly — both stages short-circuit on cache hits, so a warm
cache makes the call essentially free.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from sift.pipeline.resolver.cache import ResolverCache
from sift.pipeline.resolver.disambiguator import LLMDisambiguator
from sift.pipeline.resolver.enricher import ProductEnricher
from sift.pipeline.resolver.models import ProductProfile, SourceRef
from sift.pipeline.resolver.search.base import SearchClient
from sift.pipeline.resolver.sources import build_default_resolvers
from sift.pipeline.resolver.sources.base import SourceResolver

logger = logging.getLogger(__name__)


class ResolverPipeline:
    def __init__(
        self,
        cache: ResolverCache,
        enricher: ProductEnricher,
        resolvers: Dict[str, SourceResolver],
        fallback_search: Optional[SearchClient] = None,
    ):
        self.cache = cache
        self.enricher = enricher
        self.resolvers = resolvers
        self.fallback_search = fallback_search

    @classmethod
    def build(
        cls,
        cache_path: str,
        cache_ttl_days: int,
        primary_search: Optional[SearchClient],
        disambiguator: Optional[LLMDisambiguator],
        fallback_search: Optional[SearchClient] = None,
    ) -> "ResolverPipeline":
        cache = ResolverCache(cache_path, ttl_days=cache_ttl_days)
        enricher = ProductEnricher(primary_search)
        resolvers = build_default_resolvers(primary_search, disambiguator)
        return cls(cache, enricher, resolvers, fallback_search)

    # -- Public API -----------------------------------------------------

    def get_profile(self, name: str, hint_url: Optional[str] = None) -> ProductProfile:
        cached = self.cache.get_profile(name)
        if cached:
            return cached.value
        profile = self.enricher.enrich(name, hint_url=hint_url)
        self.cache.put_profile(profile)
        return profile

    def resolve_source(
        self, profile: ProductProfile, source: str
    ) -> Optional[SourceRef]:
        cached = self.cache.get_ref(source, profile)
        if cached:
            return cached.value
        resolver = self.resolvers.get(source)
        if resolver is None:
            return None
        ref = resolver.resolve(profile)
        if ref is None and self.fallback_search is not None:
            # Swap the primary search backend for the sitemap and retry once.
            ref = self._resolve_with_fallback(profile, resolver)
        if ref is not None:
            self.cache.put_ref(profile, ref)
        return ref

    def resolve(
        self,
        name: str,
        sources: List[str],
        hint_url: Optional[str] = None,
    ) -> Dict[str, Optional[SourceRef]]:
        profile = self.get_profile(name, hint_url=hint_url)
        out: Dict[str, Optional[SourceRef]] = {}
        for source in sources:
            try:
                out[source] = self.resolve_source(profile, source)
            except Exception as e:
                logger.warning("Resolver for '%s' raised: %s", source, e)
                out[source] = None
        return out

    # -- Internals ------------------------------------------------------

    def _resolve_with_fallback(
        self, profile: ProductProfile, resolver: SourceResolver
    ) -> Optional[SourceRef]:
        original = resolver.search_client
        try:
            resolver.search_client = self.fallback_search
            ref = resolver.resolve(profile)
            if ref:
                ref.resolver_used = (ref.resolver_used or "") + "+sitemap"
            return ref
        finally:
            resolver.search_client = original
