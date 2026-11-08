import logging
from typing import Dict, List

from sift.models import ComparisonReport, ProductReport
from sift.config import LLMConfig
from sift.pipeline._llm_base import (
    BaseLLMPipeline,
    clean_string,
    normalize_string_list,
)

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are Sift's competitive product research analyst.

Your job is to compare product pain points using only the provided cluster summaries.
Do not invent facts, market share, competitors, features, or causes.
Return exactly one JSON object. Do not use markdown, commentary, or code fences."""


COMPARISON_PROMPT = """TASK
Compare these products based on clustered user pain points.

PRODUCTS
{products}

INPUT
Product summaries:
{product_summaries}

DEFINITIONS
- shared_pain_points: issues semantically present across all compared products
- unique_pain_points: issues present for one product and absent from the others
- competitive_insights: strategic comparison based only on the listed pain points

OUTPUT SCHEMA
{{
  "shared_pain_points": ["shared issue 1", "shared issue 2"],
  "unique_pain_points": {{
    "{{product_1}}": ["unique issue 1", "unique issue 2"],
    "{{product_2}}": ["unique issue 1", "unique issue 2"]
  }},
  "competitive_insights": "3-4 sentence strategic comparison insight"
}}

RULES
- unique_pain_points keys must exactly match the product names in PRODUCTS.
- Use empty lists when there are no shared or unique pain points.
- Do not include products not listed in PRODUCTS.
- Return only the JSON object."""


_COMPARISON_SCHEMA = """{
  "shared_pain_points": ["shared issue 1", "shared issue 2"],
  "unique_pain_points": {
    "Product Name": ["unique issue 1", "unique issue 2"]
  },
  "competitive_insights": "3-4 sentence strategic comparison insight"
}"""


class Comparator(BaseLLMPipeline):
    SYSTEM_PROMPT = SYSTEM_PROMPT
    FALLBACK_NOUN = "comparison"

    def compare(self, reports: Dict[str, ProductReport]) -> ComparisonReport:
        products = list(reports.keys())
        if self.client is None:
            self._warn_unavailable_once()
            return self._fallback_comparison(products, reports, self._unavailable_reason)

        product_summaries = ""
        for name, report in reports.items():
            cluster_info = "\n".join(
                f"  - [{c.severity}] {c.label}: {c.summary}"
                for c in report.clusters
            )
            product_summaries += f"\n{name}:\n{cluster_info}\n  Overall: {report.overall_insights}\n"

        prompt = COMPARISON_PROMPT.format(
            products=", ".join(products),
            product_summaries=product_summaries,
        )

        try:
            raw = self._call_llm(prompt)
            result = self._parse_or_repair(raw, "comparison", _COMPARISON_SCHEMA)
            normalized = _normalize_comparison_result(result, products)
            return ComparisonReport(
                products=products,
                product_reports=reports,
                shared_pain_points=normalized["shared_pain_points"],
                unique_pain_points=normalized["unique_pain_points"],
                competitive_insights=normalized["competitive_insights"],
            )
        except Exception as exc:
            logger.warning(
                "LLM comparison failed for %s (%s: %s), using cluster label intersection as fallback.",
                ", ".join(products),
                type(exc).__name__,
                exc,
            )
            return self._fallback_comparison(products, reports, "LLM call failed")

    @staticmethod
    def _fallback_comparison(
        products: List[str],
        reports: Dict[str, ProductReport],
        reason: str,
    ) -> ComparisonReport:
        all_labels: Dict[str, List[str]] = {
            name: [c.label or f"Cluster {c.cluster_id}" for c in report.clusters]
            for name, report in reports.items()
        }
        label_sets = [set(labels) for labels in all_labels.values()]
        shared = set.intersection(*label_sets) if label_sets else set()
        unique = {
            name: [label for label in labels if label not in shared]
            for name, labels in all_labels.items()
        }
        return ComparisonReport(
            products=products,
            product_reports=reports,
            shared_pain_points=list(shared),
            unique_pain_points=unique,
            competitive_insights=f"Comparison analysis unavailable ({reason})",
        )


def _normalize_comparison_result(result: dict, products: List[str]) -> dict:
    raw_unique = result.get("unique_pain_points")
    raw_unique = raw_unique if isinstance(raw_unique, dict) else {}
    unique = {
        product: normalize_string_list(raw_unique.get(product))
        for product in products
    }
    return {
        "shared_pain_points": normalize_string_list(result.get("shared_pain_points")),
        "unique_pain_points": unique,
        "competitive_insights": clean_string(result.get("competitive_insights")),
    }
