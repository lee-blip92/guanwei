"""App Store ID resolver using iTunes' own search API.

iTunes Search is free, unauthenticated, structured, and globally
indexed — no Brave call needed. We bypass the generic web-search
backend here and hit ``itunes.apple.com/search`` directly.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from sift.pipeline.http_client import CurlCffiSession
from sift.pipeline.resolver.disambiguator import LLMDisambiguator
from sift.pipeline.resolver.models import (
    ProductProfile,
    ResolverCandidate,
    SourceRef,
)
from sift.pipeline.resolver.search.base import SearchClient
from sift.pipeline.resolver.sources.base import SourceResolver, slug_token_set

logger = logging.getLogger(__name__)


class AppStoreResolver(SourceResolver):
    def __init__(
        self,
        search_client: Optional[SearchClient] = None,
        disambiguator: Optional[LLMDisambiguator] = None,
    ):
        super().__init__(search_client, disambiguator)
        self._http = CurlCffiSession()

    @property
    def source_name(self) -> str:
        return "app_store"

    def resolve(self, profile: ProductProfile) -> Optional[SourceRef]:
        params = {
            "term": profile.name,
            "entity": "software",
            "limit": 10,
            "country": "us",
        }
        resp = self._http.get("https://itunes.apple.com/search", params=params, timeout=15)
        if resp is None or resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception:
            return None
        results = (data or {}).get("results", []) or []
        candidates = self._collect(profile, results)
        return self._disambiguate(profile, candidates)

    def _collect(self, profile: ProductProfile, results: list) -> List[ResolverCandidate]:
        name_tokens = slug_token_set(profile.name)
        candidates: List[ResolverCandidate] = []
        for rank, item in enumerate(results, start=1):
            app_id = item.get("trackId")
            if not app_id:
                continue
            track_name = item.get("trackName", "")
            seller = item.get("sellerName", "")
            overlap = len(name_tokens & slug_token_set(track_name)) / max(1, len(name_tokens))
            score = (1.0 - 0.1 * (rank - 1)) * (0.3 + 0.7 * overlap)
            ref = SourceRef(
                source=self.source_name,
                identifier=str(app_id),
                url=item.get("trackViewUrl"),
                payload={
                    "app_id": str(app_id),
                    "bundle_id": item.get("bundleId"),
                    "seller": seller,
                },
                confidence=score,
                resolver_used="itunes",
            )
            candidates.append(
                ResolverCandidate(
                    ref=ref,
                    evidence_title=f"{track_name} by {seller}",
                    evidence_snippet=(item.get("description") or "")[:200],
                    raw_score=score,
                )
            )
        return candidates
