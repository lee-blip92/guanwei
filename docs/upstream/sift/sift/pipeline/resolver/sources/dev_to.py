"""Dev.to tag / article resolver.

Dev.to doesn't have product pages — discussion lives under tags
(``dev.to/t/<tag>``) and article URLs. We resolve to the most likely
tag and surface the top article URLs as auxiliary payload for the
scraper to crawl.
"""

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
    slug_join,
    slug_token_set,
)


class DevToResolver(SourceResolver):
    @property
    def source_name(self) -> str:
        return "dev_to"

    def resolve(self, profile: ProductProfile) -> Optional[SourceRef]:
        tag_hits = self._site_search(
            f'site:dev.to/t "{profile.name}"', limit=5
        )
        article_hits = self._site_search(
            f'site:dev.to "{profile.name}" {profile.category or "review"}',
            limit=10,
        )

        tag_candidates = self._collect_tag_candidates(profile, tag_hits)
        if not tag_candidates:
            # Synthesize a tag from the product name itself — Dev.to
            # auto-creates tags from slugs, so a name-derived guess is
            # often correct even without a direct match.
            tag = slug_join(profile.name) or profile.name.lower().replace(" ", "")
            article_urls = [h.url for h in article_hits if host_matches(h.url, "dev.to")]
            return SourceRef(
                source=self.source_name,
                identifier=tag,
                url=f"https://dev.to/t/{tag}",
                payload={"tag": tag, "article_urls": article_urls[:10]},
                confidence=0.3,
                resolver_used="brave-fallback",
            )

        picked = self._disambiguate(profile, tag_candidates)
        if picked is None:
            return None
        article_urls = [h.url for h in article_hits if host_matches(h.url, "dev.to")]
        picked.payload["article_urls"] = article_urls[:10]
        return picked

    def _collect_tag_candidates(
        self, profile: ProductProfile, hits: list
    ) -> List[ResolverCandidate]:
        name_tokens = slug_token_set(profile.name)
        seen: set[str] = set()
        candidates: List[ResolverCandidate] = []
        for hit in hits:
            if not host_matches(hit.url, "dev.to"):
                continue
            tag = parse_path_segment(hit.url, "/t/")
            if not tag or tag in seen:
                continue
            seen.add(tag)
            overlap = len(name_tokens & slug_token_set(tag)) / max(1, len(name_tokens))
            score = (1.0 - 0.1 * (hit.rank - 1)) * (0.5 + 0.5 * overlap)
            ref = SourceRef(
                source=self.source_name,
                identifier=tag,
                url=f"https://dev.to/t/{tag}",
                payload={"tag": tag},
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
