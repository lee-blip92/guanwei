"""Dataclasses used across the resolver pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class SearchHit:
    """One result from a web search backend."""

    url: str
    title: str
    snippet: str
    rank: int = 0


@dataclass
class ProductProfile:
    """Auto-derived semantic identity of a product.

    Produced by the enricher from a bare product name plus optional
    homepage hint. Consumed by every per-source resolver as
    disambiguation context. Cached in SQLite so it's derived once.
    """

    name: str
    homepage: Optional[str] = None
    description: str = ""
    category: str = ""
    site_name: str = ""
    search_snippets: List[str] = field(default_factory=list)
    raw_metadata: Dict[str, Any] = field(default_factory=dict)
    enricher_used: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "homepage": self.homepage,
            "description": self.description,
            "category": self.category,
            "site_name": self.site_name,
            "search_snippets": list(self.search_snippets),
            "raw_metadata": dict(self.raw_metadata),
            "enricher_used": self.enricher_used,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProductProfile":
        return cls(
            name=data["name"],
            homepage=data.get("homepage"),
            description=data.get("description", ""),
            category=data.get("category", ""),
            site_name=data.get("site_name", ""),
            search_snippets=list(data.get("search_snippets", [])),
            raw_metadata=dict(data.get("raw_metadata", {})),
            enricher_used=data.get("enricher_used", ""),
        )


@dataclass
class SourceRef:
    """Source-specific canonical identifier resolved from a ProductProfile.

    The semantic value lives in ``identifier`` (a slug, URL, repo, tag,
    or numeric ID depending on the source); ``payload`` carries any
    auxiliary fields a scraper might need (e.g. country code for App
    Store, secondary tags for Dev.to).
    """

    source: str
    identifier: str
    url: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    resolver_used: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "identifier": self.identifier,
            "url": self.url,
            "payload": dict(self.payload),
            "confidence": self.confidence,
            "resolver_used": self.resolver_used,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SourceRef":
        return cls(
            source=data["source"],
            identifier=data["identifier"],
            url=data.get("url"),
            payload=dict(data.get("payload", {})),
            confidence=float(data.get("confidence", 0.0)),
            resolver_used=data.get("resolver_used", ""),
        )


@dataclass
class ResolverCandidate:
    """A possible SourceRef plus the evidence behind it.

    Emitted by per-source resolvers before disambiguation. The
    disambiguator picks one of these to promote to the final SourceRef.
    """

    ref: SourceRef
    evidence_title: str = ""
    evidence_snippet: str = ""
    raw_score: float = 0.0


@dataclass
class CachedEntry:
    """A row read from the resolver cache, with its freshness data."""

    value: Any
    resolved_at: datetime
    resolver_used: str
