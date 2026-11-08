"""GitHub repository resolver for the github_issues scraper."""

from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urlparse

from sift.pipeline.resolver.models import (
    ProductProfile,
    ResolverCandidate,
    SourceRef,
)
from sift.pipeline.resolver.sources.base import SourceResolver, slug_token_set


_GITHUB_REPO_PATH = re.compile(r"^/([^/]+)/([^/]+)/?$")


class GitHubIssuesResolver(SourceResolver):
    @property
    def source_name(self) -> str:
        return "github_issues"

    def resolve(self, profile: ProductProfile) -> Optional[SourceRef]:
        query = self._build_query(profile)
        hits = self._site_search(query, limit=10)
        candidates = self._collect(profile, hits)
        return self._disambiguate(profile, candidates)

    def _build_query(self, profile: ProductProfile) -> str:
        parts = [f'site:github.com "{profile.name}"']
        if profile.category:
            parts.append(profile.category)
        parts.append("repository")
        return " ".join(parts)

    def _collect(self, profile: ProductProfile, hits: list) -> List[ResolverCandidate]:
        name_tokens = slug_token_set(profile.name)
        seen: set[str] = set()
        candidates: List[ResolverCandidate] = []
        for hit in hits:
            parsed = urlparse(hit.url)
            if (parsed.hostname or "").removeprefix("www.") != "github.com":
                continue
            match = _GITHUB_REPO_PATH.match(parsed.path)
            if not match:
                continue
            owner, repo = match.group(1), match.group(2)
            # Skip non-repo namespaces.
            if owner.lower() in {"sponsors", "marketplace", "topics", "trending", "settings"}:
                continue
            full = f"{owner}/{repo}"
            if full in seen:
                continue
            seen.add(full)
            repo_tokens = slug_token_set(repo)
            owner_tokens = slug_token_set(owner)
            overlap = len(name_tokens & (repo_tokens | owner_tokens)) / max(1, len(name_tokens))
            score = (1.0 - 0.1 * (hit.rank - 1)) * (0.4 + 0.6 * overlap)
            ref = SourceRef(
                source=self.source_name,
                identifier=full,
                url=f"https://github.com/{full}",
                payload={"repo": full, "owner": owner, "name": repo},
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
