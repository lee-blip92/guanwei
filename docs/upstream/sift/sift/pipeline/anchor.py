"""Layer 2 of the content disambiguator: embedding anchor similarity.

A product's semantic identity is captured as a single "anchor" embedding built
from its description, category, homepage title, and a couple of search snippets.
Each candidate item's embedding is compared (cosine) against this anchor; the
result is fused with the Layer 1 heuristic score into a ``combined`` score that
drives triage.

For common-word ("ambiguous") products the keyword signal is unreliable, so the
weights flip toward the anchor — semantic alignment is the better evidence when
"notion" might be the product or just the English word.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from sift.config import DisambiguatorConfig
from sift.models import ProductContext
from sift.pipeline.embedder import Embedder
from sift.pipeline.resolver.models import ProductProfile

logger = logging.getLogger(__name__)


class ProductAnchor:
    """Builds and embeds the product's semantic anchor text."""

    def __init__(self, embedder: Embedder):
        self.embedder = embedder

    @staticmethod
    def build_text(profile: Optional[ProductProfile], context: ProductContext) -> str:
        """Anchor text: rich resolver profile when available, else ProductContext.

        Rich: description + category + homepage title + top-2 search snippets.
        Fallback: ProductContext description + category (always available).
        """
        if profile is not None:
            homepage_title = (
                profile.raw_metadata.get("og:title")
                or profile.raw_metadata.get("title")
                or profile.site_name
                or ""
            )
            parts = [
                profile.name,
                profile.description,
                profile.category,
                homepage_title,
                *profile.search_snippets[:2],
            ]
        else:
            parts = [context.canonical_name, context.description, context.category]
        return " ".join(p.strip() for p in parts if p and p.strip()).strip()

    def vector(
        self, profile: Optional[ProductProfile], context: ProductContext
    ) -> Optional[np.ndarray]:
        """Return the L2-normalized anchor embedding, or None if no anchor text."""
        text = self.build_text(profile, context)
        if not text:
            logger.info(
                "No anchor text for '%s'; Layer 2 will fall back to heuristic score.",
                context.canonical_name,
            )
            return None
        return self.embedder.embed_profile(text)


class AnchorSimilarityFilter:
    """Fuses Layer 1 heuristic scores with anchor cosine similarity."""

    def __init__(self, config: DisambiguatorConfig):
        self.config = config

    def combined(
        self,
        item_embedding: np.ndarray,
        anchor: Optional[np.ndarray],
        heuristic_score: float,
        ambiguous: bool,
    ) -> tuple[Optional[float], float]:
        """Return ``(anchor_score, combined_score)``.

        ``anchor_score`` is cosine similarity normalized to 0-1; it is ``None``
        when no anchor exists, in which case ``combined`` is the heuristic score
        alone. Both embeddings are unit-normalized, so cosine is a dot product.
        """
        if anchor is None:
            return None, heuristic_score

        cosine = float(np.dot(item_embedding, anchor))
        anchor_score = max(0.0, min(1.0, (cosine + 1.0) / 2.0))

        w_anchor = (
            self.config.anchor_weight_ambiguous
            if ambiguous
            else self.config.anchor_weight_normal
        )
        w_heuristic = 1.0 - w_anchor
        combined = heuristic_score * w_heuristic + anchor_score * w_anchor
        return anchor_score, combined
