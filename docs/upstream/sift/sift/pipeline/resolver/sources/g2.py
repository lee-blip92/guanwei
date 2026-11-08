"""G2 product page resolver."""

from __future__ import annotations

from typing import List, Optional

from sift.pipeline.resolver.models import (
    ProductProfile,
    ResolverCandidate,
    SourceRef,
)
from sift.pipeline.resolver.sources.base import (
    SourceResolver,
    host_matches,
    parse_path_segment,
    slug_token_set,
)


class G2Resolver(SourceResolver):
    @property
    def source_name(self) -> str:
        return "g2"

    def resolve(self, profile: ProductProfile) -> Optional[SourceRef]:
        query = self._build_query(profile)
        hits = self._site_search(query, limit=10)
        candidates = self._collect(profile, hits)
        return self._disambiguate(profile, candidates)

    def _build_query(self, profile: ProductProfile) -> str:
        parts = [f'site:g2.com/products "{profile.name}"']
        if profile.category:
            parts.append(profile.category)
        return " ".join(parts)

    def _collect(self, profile: ProductProfile, hits: list) -> List[ResolverCandidate]:
        name_tokens = slug_token_set(profile.name)
        seen: set[str] = set()
        candidates: List[ResolverCandidate] = []
        for hit in hits:
            if not host_matches(hit.url, "g2.com"):
                continue
            slug = parse_path_segment(hit.url, "/products/")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            slug_tokens = slug_token_set(slug)
            overlap = len(name_tokens & slug_tokens) / max(1, len(name_tokens))
            score = (1.0 - 0.1 * (hit.rank - 1)) * (0.5 + 0.5 * overlap)
            product_url = f"https://www.g2.com/products/{slug}"
            ref = SourceRef(
                source=self.source_name,
                identifier=slug,
                url=product_url,
                payload={"slug": slug, "reviews_url": f"{product_url}/reviews"},
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
