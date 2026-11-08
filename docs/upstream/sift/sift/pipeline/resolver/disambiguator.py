"""LLM tiebreaker for ambiguous resolutions.

Per-source resolvers emit ``ResolverCandidate`` lists. When more than
one candidate is plausible (e.g. two PH products both named "Notion"),
the disambiguator hands the candidates to the configured LLM along
with the ``ProductProfile`` context and asks for the best match.

Reuses the existing OpenAI-compatible client construction from
``sift/pipeline/analyzer.py`` rather than introducing a parallel LLM
stack.
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from openai import OpenAI

from sift.config import LLMConfig
from sift.pipeline.llm_client import LLMRequestOptions, create_chat_completion
from sift.pipeline.resolver.models import ProductProfile, ResolverCandidate

logger = logging.getLogger(__name__)


_DISAMBIGUATE_PROMPT = """You are matching a product name to its canonical entry on a third-party site.

PRODUCT IDENTITY:
- Name: {name}
- Homepage: {homepage}
- Description: {description}
- Category: {category}
- Web snippets:
{snippets}

CANDIDATES (each is a possible match on the same third-party site):
{candidates}

Pick the single candidate that best matches the PRODUCT IDENTITY above.
Respond with ONLY a JSON object: {{"index": <0-based candidate index>, "reason": "<one short sentence>"}}
If none of the candidates match the product, respond with {{"index": -1, "reason": "<why>"}}.
"""


class LLMDisambiguator:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.client: Optional[OpenAI] = None
        if (config.api_key or "").strip():
            try:
                self.client = OpenAI(api_key=config.api_key, base_url=config.base_url)
            except Exception as e:
                logger.warning("LLMDisambiguator client init failed: %s", e)

    @property
    def is_available(self) -> bool:
        return self.client is not None

    def pick(
        self, profile: ProductProfile, candidates: List[ResolverCandidate]
    ) -> Optional[ResolverCandidate]:
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        if not self.is_available:
            # No LLM configured — fall through to "pick top" semantics.
            return max(candidates, key=lambda c: c.raw_score)

        prompt = _DISAMBIGUATE_PROMPT.format(
            name=profile.name,
            homepage=profile.homepage or "(unknown)",
            description=profile.description or "(none)",
            category=profile.category or "(unknown)",
            snippets="\n".join(f"  - {s}" for s in profile.search_snippets[:5]) or "  (none)",
            candidates="\n".join(
                f"  [{i}] {c.evidence_title} — {c.evidence_snippet} ({c.ref.url or c.ref.identifier})"
                for i, c in enumerate(candidates)
            ),
        )

        # Cap disambiguator output: the reply is a one-line JSON object, so
        # the full llm.max_tokens budget (often thousands) is wasted spend
        # and on reasoning-capable models actively pulls long chains-of-thought.
        options = LLMRequestOptions(
            temperature=0.0,
            max_tokens=min(512, self.config.max_tokens),
        )
        content = ""
        try:
            response = create_chat_completion(
                self.client,
                self.config.model,
                [{"role": "user", "content": prompt}],
                options,
                logger,
            )
            choice = response.choices[0]
            content = choice.message.content or ""
            if not content:
                finish_reason = getattr(choice, "finish_reason", "unknown")
                logger.warning(
                    "LLMDisambiguator got empty content (finish_reason=%r); retrying without temperature",
                    finish_reason,
                )
                # Some proxies silently return empty content for temperature=0.0 — retry once.
                options._temperature_enabled = False
                response = create_chat_completion(
                    self.client,
                    self.config.model,
                    [{"role": "user", "content": prompt}],
                    options,
                    logger,
                )
                content = response.choices[0].message.content or ""
        except Exception as e:
            logger.warning("LLMDisambiguator call failed: %s", e)
            return max(candidates, key=lambda c: c.raw_score)

        idx = _extract_index(content)
        if idx is None:
            logger.warning("LLMDisambiguator could not parse: %r", content[:200])
            return max(candidates, key=lambda c: c.raw_score)
        if idx == -1:
            logger.info("LLMDisambiguator rejected all candidates for '%s'", profile.name)
            return None
        if 0 <= idx < len(candidates):
            picked = candidates[idx]
            picked.ref.resolver_used = f"{picked.ref.resolver_used}+llm"
            return picked
        return max(candidates, key=lambda c: c.raw_score)


def _extract_index(content: str) -> Optional[int]:
    content = content.strip()
    # Find the first JSON object in the response — models often add prose.
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    idx = data.get("index")
    if isinstance(idx, int):
        return idx
    return None
