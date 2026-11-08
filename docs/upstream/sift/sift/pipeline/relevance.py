"""Layer 1 of the content disambiguator: cheap heuristic scoring.

`RelevanceFilter` no longer makes the final keep/reject call — that now belongs
to the three-layer `ContentDisambiguator` (heuristics -> anchor similarity ->
LLM gate). Layer 1's job is to assign each item a 0-1 heuristic relevance score
and drop only obvious garbage (`heuristic_hard_reject`, default 0.10) before the
expensive embedding step. Everything else flows downstream for semantic
adjudication.

For ambiguous products (common-word names like "Notion" / "Signal"), Layer 1
also applies auto-negative phrase penalties and only grants the full alias bonus
when the alias appears in a structural position (URL/metadata/title) rather than
buried in common-word prose.
"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from sift.config import DisambiguatorConfig
from sift.models import FeedbackItem, ProductContext
from sift.pipeline.ambiguous_words import is_ambiguous_name

logger = logging.getLogger(__name__)


# Sources where a bare product mention is much more likely to be the product
# than the common word (a "Notion" GitHub issue vs "notion" in a SO answer).
_DEV_SOURCES = frozenset({"github_issues", "stack_overflow", "dev_to", "hacker_news"})
_DEV_CATEGORY_MARKERS = ("developer", "ai coding agent", "developer-tools", "devtool")

_WEBSITE_BONUS = 0.35


@dataclass(frozen=True)
class Layer1Stats:
    total: int
    dropped: int
    kept: int


class RelevanceFilter:
    """Layer 1 heuristic scorer for the content disambiguator."""

    def __init__(self, config: DisambiguatorConfig):
        self.config = config

    def prefilter(
        self,
        items: list[FeedbackItem],
        context: ProductContext,
    ) -> tuple[list[FeedbackItem], Layer1Stats]:
        """Score every item and drop those below ``heuristic_hard_reject``.

        Survivors keep their ``relevance_score`` in metadata for downstream
        layers (anchor combine, top-N truncation, safety net).
        """
        if not self.config.enabled:
            return items, Layer1Stats(total=len(items), dropped=0, kept=len(items))

        ambiguous = is_ambiguous_name(context.canonical_name)
        kept: list[FeedbackItem] = []
        for item in items:
            score = self.score(item, context, ambiguous)
            if score >= self.config.heuristic_hard_reject:
                kept.append(item)
            elif not item.metadata.get("rejection_reason"):
                item.metadata["rejection_reason"] = (
                    f"layer-1 heuristic score {score:.2f} below "
                    f"{self.config.heuristic_hard_reject:.2f}"
                )

        dropped = len(items) - len(kept)
        if dropped:
            logger.info(
                "Layer 1 dropped %d/%d garbage item(s) for '%s'.",
                dropped,
                len(items),
                context.canonical_name,
            )
        return kept, Layer1Stats(total=len(items), dropped=dropped, kept=len(kept))

    def score(self, item: FeedbackItem, context: ProductContext, ambiguous: bool) -> float:
        text = _normalize_text(item.text)
        url = _normalize_text(item.url or "")
        metadata_text = _normalize_text(" ".join(_flatten_metadata(item.metadata)))
        structural_text = f"{url} {metadata_text}".strip()

        matched_aliases = _matches_terms(text, context.search_terms)
        matched_identifiers = _matches_identifiers(item, context)
        matched_structural = _matches_terms(structural_text, context.search_terms)
        matched_context = _matches_context_terms(text, context)
        matched_negatives = _matches_terms(text, context.negative_terms)
        matched_auto_negatives = _auto_negative_matches(text, context, ambiguous)

        score = 0.0
        reasons: list[str] = []

        if matched_identifiers:
            score += self.config.identifier_weight
            reasons.extend(f"identifier:{term}" for term in matched_identifiers)

        if matched_aliases:
            # For ambiguous names a body-only alias mention is weak: grant the
            # full alias weight only when it also appears structurally.
            if ambiguous and not matched_structural:
                score += self.config.alias_weight * 0.5
                reasons.append("alias:body-only(ambiguous)")
            else:
                score += self.config.alias_weight
            reasons.extend(f"alias:{term}" for term in matched_aliases)

        if matched_structural:
            score += self.config.structural_context_bonus
            reasons.extend(f"structural:{term}" for term in matched_structural)

        if context.website and _host_matches(item.url or "", context.website):
            score += _WEBSITE_BONUS
            reasons.append("website")

        if matched_context:
            score += min(0.25, 0.08 * len(matched_context))
            reasons.extend(f"context:{term}" for term in matched_context)

        if _is_dev_source(item.source) and _is_dev_category(context.category):
            score += self.config.source_type_bonus
            reasons.append(f"source_type:{item.source}")

        if matched_negatives:
            score -= min(0.55, 0.18 * len(matched_negatives))
            reasons.extend(f"negative:{term}" for term in matched_negatives)

        if matched_auto_negatives:
            score -= self.config.auto_negative_penalty * len(matched_auto_negatives)
            reasons.extend(f"auto_negative:{p}" for p in matched_auto_negatives)

        score = max(0.0, min(1.0, score))
        item.metadata["relevance_score"] = round(score, 3)
        item.metadata["matched_aliases"] = matched_aliases
        item.metadata["matched_identifiers"] = matched_identifiers
        item.metadata["matched_context_terms"] = matched_context
        if matched_negatives:
            item.metadata["negative_terms"] = matched_negatives
        if matched_auto_negatives:
            item.metadata["auto_negative_patterns"] = matched_auto_negatives
        item.metadata["relevance_reasons"] = reasons
        return score


def _is_dev_source(source: str) -> bool:
    return (source or "").casefold() in _DEV_SOURCES


def _is_dev_category(category: str) -> bool:
    cat = _normalize_text(category)
    return any(marker in cat for marker in _DEV_CATEGORY_MARKERS)


def _auto_negative_matches(text: str, context: ProductContext, ambiguous: bool) -> list[str]:
    """Common-word constructions that never refer to the product.

    Only applied to ambiguous (common-word) names — for "Notion", phrases like
    "the notion of" or "any notion" are ordinary English, not the product.
    """
    if not ambiguous:
        return []
    name = _normalize_text(context.canonical_name)
    if not name:
        return []
    patterns = [f"the {name} of", f"the {name} that", f"any {name}", f"{name} as a"]
    return [p for p in patterns if p in text]


def _matches_identifiers(item: FeedbackItem, context: ProductContext) -> list[str]:
    values = _normalize_text(" ".join([
        item.url or "",
        *(_flatten_metadata(item.metadata)),
    ]))
    return [
        term
        for term in context.identifiers
        if _normalize_text(term) and _normalize_text(term) in values
    ]


def _matches_context_terms(text: str, context: ProductContext) -> list[str]:
    terms = [context.category, *re.split(r"[\s,/.-]+", context.description)]
    useful = [
        term
        for term in terms
        if len(term.strip()) >= 4
    ]
    return _matches_terms(text, useful)


def _matches_terms(text: str, terms: tuple[str, ...] | list[str]) -> list[str]:
    matches: list[str] = []
    for term in terms:
        normalized = _normalize_text(term)
        if not normalized:
            continue
        if _term_in_text(normalized, text):
            matches.append(term)
    return matches


def _term_in_text(term: str, text: str) -> bool:
    if not term:
        return False
    if " " in term or "-" in term or "_" in term:
        return term in text
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


def _flatten_metadata(metadata: dict) -> list[str]:
    values: list[str] = []
    for value in metadata.values():
        if isinstance(value, (str, int, float)):
            values.append(str(value))
        elif isinstance(value, list):
            values.extend(str(v) for v in value if isinstance(v, (str, int, float)))
        elif isinstance(value, dict):
            values.extend(str(v) for v in value.values() if isinstance(v, (str, int, float)))
    return values


def _host_matches(url: str, website: str) -> bool:
    if not url or not website:
        return False
    url_host = urlparse(url).netloc.casefold()
    website_host = urlparse(website if "://" in website else f"https://{website}").netloc.casefold()
    return bool(url_host and website_host and url_host.endswith(website_host))


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).casefold()).strip()
