"""Three-layer content disambiguator orchestrator.

Coordinates the three layers that decide which scraped items are genuinely
about the product:

1. :class:`~sift.pipeline.relevance.RelevanceFilter` — cheap heuristic scoring
   (``prefilter``), drops obvious garbage before any embedding work.
2. :class:`~sift.pipeline.anchor.AnchorSimilarityFilter` — fuses the heuristic
   score with cosine similarity to the product anchor embedding.
3. :class:`~sift.pipeline.llm_gate.LLMGate` — adjudicates only the uncertain
   middle band with a batched LLM yes/no call.

The orchestrator embeds survivors exactly once and returns those embeddings
sliced to the kept items, so clustering reuses them with no recomputation.

Two phases are exposed so the cli can run id-based dedup and per-source
truncation (which are session-stateful) *between* the cheap heuristics and the
expensive embedding step:

- ``prefilter`` — Layer 1 only (used by both the analyze and scrape paths).
- ``resolve``   — Layers 2 + 3 on the post-dedup, post-truncation survivors
  (analyze path only).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from sift.config import ClusteringConfig, DisambiguatorConfig, LLMConfig
from sift.models import FeedbackItem, ProductContext
from sift.pipeline.ambiguous_words import is_ambiguous_name
from sift.pipeline.anchor import AnchorSimilarityFilter, ProductAnchor
from sift.pipeline.embedder import Embedder
from sift.pipeline.llm_gate import LLMGate
from sift.pipeline.relevance import Layer1Stats, RelevanceFilter
from sift.pipeline.resolver.models import ProductProfile

logger = logging.getLogger(__name__)


@dataclass
class DisambiguationStats:
    total_in: int = 0            # items entering Layer 2 (post L1/dedup/truncate)
    anchor_used: bool = False
    auto_kept: int = 0           # combined > keep threshold
    auto_rejected: int = 0       # combined < reject threshold or anchor floor
    gated: int = 0               # routed to the LLM gate
    gate_yes: int = 0
    gate_no: int = 0
    over_cap: int = 0            # uncertain but decided by score (cap/no-LLM)
    final_kept: int = 0
    safety_net_triggered: bool = False


@dataclass
class DisambiguationResult:
    kept_items: list[FeedbackItem]
    kept_embeddings: np.ndarray
    stats: DisambiguationStats


class ContentDisambiguator:
    def __init__(
        self,
        config: DisambiguatorConfig,
        llm_config: LLMConfig,
        clustering_config: ClusteringConfig,
    ):
        self.config = config
        self.clustering_config = clustering_config
        self.scorer = RelevanceFilter(config)
        self.layer2 = AnchorSimilarityFilter(config)
        self.gate = LLMGate(config, llm_config)
        self._embedder: Optional[Embedder] = None
        self._anchor: Optional[ProductAnchor] = None

    @property
    def embedder(self) -> Embedder:
        """Lazily construct the embedder so an empty run never loads the model."""
        if self._embedder is None:
            self._embedder = Embedder(self.clustering_config)
            self._anchor = ProductAnchor(self._embedder)
        return self._embedder

    # ── Phase 1 (both paths) ────────────────────────────────────────────
    def prefilter(
        self, items: list[FeedbackItem], context: ProductContext
    ) -> tuple[list[FeedbackItem], Layer1Stats]:
        return self.scorer.prefilter(items, context)

    def safety_net_by_score(self, items: list[FeedbackItem]) -> list[FeedbackItem]:
        """Keep the top-K items by heuristic score (Layer-1-only safety net)."""
        if not items:
            return []
        k = max(1, self.config.safety_net_min_items)
        ranked = sorted(
            items, key=lambda it: it.metadata.get("relevance_score", 0.0), reverse=True
        )
        return ranked[:k]

    # ── Phase 2 (analyze path) ──────────────────────────────────────────
    def resolve(
        self,
        items: list[FeedbackItem],
        context: ProductContext,
        profile: Optional[ProductProfile],
    ) -> DisambiguationResult:
        if not items:
            return DisambiguationResult([], np.empty((0, 0)), DisambiguationStats())

        ambiguous = is_ambiguous_name(context.canonical_name)
        embeddings = self.embedder.embed(items)
        anchor_vec = self._anchor.vector(profile, context)  # set when embedder built

        cfg = self.config
        midpoint = (cfg.llm_gate_keep_threshold + cfg.llm_gate_reject_threshold) / 2.0

        stats = DisambiguationStats(total_in=len(items), anchor_used=anchor_vec is not None)
        combined_scores: list[float] = []
        keep_idx: list[int] = []
        reject_idx: list[int] = []
        uncertain: list[tuple[int, FeedbackItem, float]] = []

        for i, item in enumerate(items):
            heuristic = float(item.metadata.get("relevance_score", 0.0))
            anchor_score, combined = self.layer2.combined(
                embeddings[i], anchor_vec, heuristic, ambiguous
            )
            combined_scores.append(combined)
            item.metadata["combined_score"] = round(combined, 3)
            item.metadata["anchor_score"] = (
                round(anchor_score, 3) if anchor_score is not None else None
            )

            if ambiguous and anchor_score is not None and anchor_score < cfg.anchor_min_similarity:
                reject_idx.append(i)
                item.metadata["disambiguation"] = "reject:anchor-floor"
                stats.auto_rejected += 1
            elif combined > cfg.llm_gate_keep_threshold:
                keep_idx.append(i)
                item.metadata["disambiguation"] = "keep:auto"
                stats.auto_kept += 1
            elif combined < cfg.llm_gate_reject_threshold:
                reject_idx.append(i)
                item.metadata["disambiguation"] = "reject:auto"
                stats.auto_rejected += 1
            else:
                uncertain.append((i, item, combined))

        # Layer 3 — LLM gate on the uncertain band only.
        verdicts, gate_stats = self.gate.adjudicate(
            [(item, combined) for _, item, combined in uncertain],
            context.canonical_name,
            context.description,
            context.category,
        )
        stats.gated = gate_stats.gated
        stats.gate_yes = gate_stats.gate_yes
        stats.gate_no = gate_stats.gate_no
        stats.over_cap = gate_stats.over_cap
        for i, item, combined in uncertain:
            keep = verdicts.get(item.id, combined >= midpoint)
            if keep:
                keep_idx.append(i)
                item.metadata["disambiguation"] = "keep:gate"
            else:
                reject_idx.append(i)
                item.metadata["disambiguation"] = "reject:gate"

        # Safety net — never return an empty set when there were candidates.
        if not keep_idx:
            stats.safety_net_triggered = True
            k = max(1, cfg.safety_net_min_items)
            keep_idx = sorted(
                range(len(items)), key=lambda i: combined_scores[i], reverse=True
            )[:k]
            for i in keep_idx:
                items[i].metadata["disambiguation"] = "keep:safety-net"

        keep_idx = sorted(set(keep_idx))
        kept_items = [items[i] for i in keep_idx]
        kept_embeddings = embeddings[keep_idx] if keep_idx else np.empty((0, embeddings.shape[1]))
        stats.final_kept = len(kept_items)

        logger.info(
            "Disambiguator '%s': %d in -> %d kept (auto-keep=%d, auto-reject=%d, "
            "gated=%d[yes=%d/no=%d], over-cap=%d, safety_net=%s).",
            context.canonical_name, stats.total_in, stats.final_kept, stats.auto_kept,
            stats.auto_rejected, stats.gated, stats.gate_yes, stats.gate_no,
            stats.over_cap, stats.safety_net_triggered,
        )
        return DisambiguationResult(kept_items, kept_embeddings, stats)
