"""Play Store package name resolver."""

from __future__ import annotations

from typing import List, Optional
from urllib.parse import parse_qs, urlparse

from sift.pipeline.resolver.models import (
    ProductProfile,
    ResolverCandidate,
    SourceRef,
)
from sift.pipeline.resolver.sources.base import (
    SourceResolver,
    host_matches,
    slug_token_set,
)


class PlayStoreResolver(SourceResolver):
    @property
    def source_name(self) -> str:
        return "play_store"

    def resolve(self, profile: ProductProfile) -> Optional[SourceRef]:
        query = f'site:play.google.com/store/apps "{profile.name}"'
        if profile.category:
            query += f" {profile.category}"
        hits = self._site_search(query, limit=10)
        candidates = self._collect(profile, hits)
        return self._disambiguate(profile, candidates)

    def _collect(self, profile: ProductProfile, hits: list) -> List[ResolverCandidate]:
        name_tokens = slug_token_set(profile.name)
        seen: set[str] = set()
        candidates: List[ResolverCandidate] = []
        for hit in hits:
            if not host_matches(hit.url, "play.google.com"):
                continue
            parsed = urlparse(hit.url)
            if not parsed.path.startswith("/store/apps/details"):
                continue
            package = parse_qs(parsed.query).get("id", [None])[0]
            if not package or package in seen:
                continue
            seen.add(package)
            overlap = len(name_tokens & slug_token_set(package)) / max(1, len(name_tokens))
            score = (1.0 - 0.1 * (hit.rank - 1)) * (0.4 + 0.6 * overlap)
            ref = SourceRef(
                source=self.source_name,
                identifier=package,
                url=f"https://play.google.com/store/apps/details?id={package}",
                payload={"package_name": package},
                confidence=score,
                resolver_used="brave",
            )
            candidates.append(
                ResolverCandidate(
                    ref=ref,
                    evidence_title=hit.title,
                    evidence_snippet=hit.snippet,
                    raw_score=score,
                )
            )
        return candidates
