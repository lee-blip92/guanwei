"""Domain models for Sift.

All dataclasses used across the scraper, pipeline, and UI layers live
here. Keeping them in a single module avoids the four-file split that
preceded this consolidation — each of those files held one or two
small dataclasses and forced a maze of cross-imports.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional


# ── Feedback ────────────────────────────────────────────────────────────


@dataclass
class FeedbackItem:
    source: str          # source key such as "g2", "hacker_news", or "github_issues"
    product: str         # product name being analyzed
    text: str            # the actual feedback/complaint text
    rating: Optional[float] = None   # numeric rating if available (e.g. G2 stars)
    url: Optional[str] = None        # clickable link to original post/review
    date: Optional[datetime] = None  # when feedback was posted
    metadata: dict = field(default_factory=dict)  # extra source-specific fields
    id: str = field(init=False)      # deterministic hash ID for deduplication

    def __post_init__(self):
        raw = f"{self.source}:{self.url or self.text}"
        self.id = hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "product": self.product,
            "text": self.text,
            "rating": self.rating,
            "url": self.url,
            "date": self.date.isoformat() if self.date else None,
            "metadata": self.metadata,
        }


# ── Clustering ──────────────────────────────────────────────────────────


@dataclass
class ClusterResult:
    cluster_id: int                      # HDBSCAN cluster label (-1 = noise)
    label: Optional[str] = None          # human-readable name (set by LLM later)
    items: List[FeedbackItem] = field(default_factory=list)
    summary: Optional[str] = None        # pain point summary (set by LLM later)
    severity: Optional[str] = None       # "high", "medium", "low" (set by LLM later)
    representative_quotes: List[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.items)

    @property
    def is_noise(self) -> bool:
        return self.cluster_id == -1

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "label": self.label,
            "summary": self.summary,
            "severity": self.severity,
            "size": self.size,
            "representative_quotes": self.representative_quotes,
            "items": [item.to_dict() for item in self.items],
        }


# ── Reports ─────────────────────────────────────────────────────────────


@dataclass
class ProductReport:
    product: str
    total_feedback_count: int
    clusters: List[ClusterResult] = field(default_factory=list)
    overall_insights: Optional[str] = None     # LLM-generated high-level summary
    top_pain_points: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "product": self.product,
            "total_feedback_count": self.total_feedback_count,
            "clusters": [c.to_dict() for c in self.clusters],
            "overall_insights": self.overall_insights,
            "top_pain_points": self.top_pain_points,
        }


@dataclass
class ComparisonReport:
    products: List[str]
    product_reports: Dict[str, ProductReport] = field(default_factory=dict)
    shared_pain_points: List[str] = field(default_factory=list)
    unique_pain_points: Dict[str, List[str]] = field(default_factory=dict)
    competitive_insights: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "products": self.products,
            "product_reports": {k: v.to_dict() for k, v in self.product_reports.items()},
            "shared_pain_points": self.shared_pain_points,
            "unique_pain_points": self.unique_pain_points,
            "competitive_insights": self.competitive_insights,
        }


# ── Product context ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProductContext:
    canonical_name: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    negative_terms: tuple[str, ...] = field(default_factory=tuple)
    category: str = ""
    description: str = ""
    website: str = ""
    repos: tuple[str, ...] = field(default_factory=tuple)
    slugs: tuple[str, ...] = field(default_factory=tuple)
    app_ids: tuple[str, ...] = field(default_factory=tuple)
    package_names: tuple[str, ...] = field(default_factory=tuple)

    @property
    def search_terms(self) -> tuple[str, ...]:
        return _unique_terms((self.canonical_name, *self.aliases))

    @property
    def identifiers(self) -> tuple[str, ...]:
        return _unique_terms((*self.repos, *self.slugs, *self.app_ids, *self.package_names))


def build_product_context(product_name: str, settings) -> ProductContext:
    profile = _lookup_profile(product_name, settings.products)
    if profile is None:
        return ProductContext(canonical_name=product_name, aliases=())

    aliases = _unique_terms((product_name, *profile.aliases))
    return ProductContext(
        canonical_name=product_name,
        aliases=aliases,
        negative_terms=_unique_terms(profile.negative_terms),
        category=profile.category,
        description=profile.description,
        website=profile.website,
        repos=tuple(profile.repos),
        slugs=tuple(profile.slugs),
        app_ids=tuple(profile.app_ids),
        package_names=tuple(profile.package_names),
    )


def _lookup_profile(product_name, profiles):
    needle = product_name.casefold()
    for name, profile in profiles.items():
        candidates = [name, *profile.aliases]
        if any(candidate.casefold() == needle for candidate in candidates):
            return profile
    return None


def _unique_terms(terms: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        cleaned = str(term).strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return tuple(result)
