"""Layer 3 of the content disambiguator: a targeted LLM yes/no gate.

Only items the cheaper layers can't decide (combined score in the uncertain
band) reach the gate. It asks the configured LLM, in small batches, whether each
text is specifically about the product, and returns a keep/reject verdict per
item. The gate is deliberately frugal:

- ``llm_gate_max_items`` caps how many items ever hit the LLM; overflow is
  decided by the combined score alone.
- Items are batched (``llm_gate_batch_size``) into one numbered prompt per call.
- No LLM configured, a failed call, or an unparseable line all fall back to the
  combined score vs. the midpoint of the keep/reject thresholds.

This keeps Sift an ML pipeline that uses the LLM for surgical disambiguation,
not a bulk LLM classifier.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI

from sift.config import DisambiguatorConfig, LLMConfig
from sift.models import FeedbackItem
from sift.pipeline.llm_client import LLMRequestOptions, create_chat_completion

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS = 300
_GATE_MAX_TOKENS = 256

_PROMPT_HEADER = (
    'Which of the following numbered texts are specifically about the product '
    '"{name}"{descriptor}?\n'
    'Reply with exactly one line per item in the form "<number>: YES" or '
    '"<number>: NO". Output nothing else.\n\n'
)

_LINE_RE = re.compile(r"(\d+)\s*[:.\)\-]\s*(yes|no)\b", re.IGNORECASE)


@dataclass
class GateStats:
    gated: int = 0          # items routed to the LLM
    gate_yes: int = 0
    gate_no: int = 0
    over_cap: int = 0       # uncertain items decided by score (cap hit / no LLM)


class LLMGate:
    def __init__(self, config: DisambiguatorConfig, llm_config: LLMConfig):
        self.config = config
        self.llm_config = llm_config
        self.client: Optional[OpenAI] = None
        if config.llm_gate_enabled and (llm_config.api_key or "").strip():
            try:
                self.client = OpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("LLMGate client init failed: %s", e)

    @property
    def is_available(self) -> bool:
        return self.client is not None

    @property
    def _midpoint(self) -> float:
        return (self.config.llm_gate_keep_threshold + self.config.llm_gate_reject_threshold) / 2.0

    def adjudicate(
        self,
        candidates: list[tuple[FeedbackItem, float]],
        name: str,
        description: str = "",
        category: str = "",
    ) -> tuple[dict[str, bool], GateStats]:
        """Decide keep/reject for uncertain ``(item, combined_score)`` pairs.

        Returns ``({item.id: keep}, stats)``. Items beyond ``llm_gate_max_items``
        or handled without an LLM fall back to ``combined >= midpoint``.
        """
        stats = GateStats()
        verdicts: dict[str, bool] = {}
        if not candidates:
            return verdicts, stats

        # Cost guard: only the first N uncertain items hit the LLM.
        to_llm = candidates[: self.config.llm_gate_max_items]
        overflow = candidates[self.config.llm_gate_max_items :]
        for item, combined in overflow:
            verdicts[item.id] = combined >= self._midpoint
            stats.over_cap += 1

        if not self.is_available:
            for item, combined in to_llm:
                verdicts[item.id] = combined >= self._midpoint
                stats.over_cap += 1
            return verdicts, stats

        descriptor = ""
        if description or category:
            inner = ", ".join(p for p in (description, category) if p)
            descriptor = f" ({inner})"

        batch_size = max(1, self.config.llm_gate_batch_size)
        for start in range(0, len(to_llm), batch_size):
            batch = to_llm[start : start + batch_size]
            decided = self._adjudicate_batch(batch, name, descriptor)
            for (item, combined), keep in zip(batch, decided):
                if keep is None:
                    verdicts[item.id] = combined >= self._midpoint  # unparsed -> fallback
                else:
                    verdicts[item.id] = keep
                    stats.gated += 1
                    if keep:
                        stats.gate_yes += 1
                    else:
                        stats.gate_no += 1
        return verdicts, stats

    def _adjudicate_batch(
        self, batch: list[tuple[FeedbackItem, float]], name: str, descriptor: str
    ) -> list[Optional[bool]]:
        """Return per-item keep verdicts; ``None`` where the LLM didn't answer."""
        prompt = _PROMPT_HEADER.format(name=name, descriptor=descriptor)
        prompt += "\n".join(
            f'{i}. "{_truncate(item.text)}"' for i, (item, _) in enumerate(batch, 1)
        )

        options = LLMRequestOptions(
            temperature=0.0,
            max_tokens=min(_GATE_MAX_TOKENS, self.llm_config.max_tokens),
        )
        try:
            response = create_chat_completion(
                self.client,
                self.llm_config.model,
                [{"role": "user", "content": prompt}],
                options,
                logger,
            )
            content = response.choices[0].message.content or ""
        except Exception as e:
            logger.warning("LLMGate call failed (%s); falling back to combined score.", e)
            return [None] * len(batch)

        if not content.strip():
            logger.warning("LLMGate returned empty content; falling back to combined score.")
            return [None] * len(batch)

        parsed = _parse_verdicts(content)
        return [parsed.get(i) for i in range(1, len(batch) + 1)]


def _truncate(text: str) -> str:
    text = " ".join((text or "").split())
    return text[:_MAX_TEXT_CHARS]


def _parse_verdicts(content: str) -> dict[int, bool]:
    verdicts: dict[int, bool] = {}
    for match in _LINE_RE.finditer(content):
        idx = int(match.group(1))
        verdicts[idx] = match.group(2).lower() == "yes"
    return verdicts
