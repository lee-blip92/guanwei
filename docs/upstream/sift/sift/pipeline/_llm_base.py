"""Shared LLM-pipeline scaffolding.

Both :class:`sift.pipeline.analyzer.Analyzer` and
:class:`sift.pipeline.comparator.Comparator` need the same set of
plumbing: build an OpenAI client (handling a missing API key
gracefully), call the chat-completions endpoint, parse the response as
JSON with a one-shot "ask the model to repair its own output" retry,
and warn once when the client is unavailable.

This module hosts that scaffolding so the two pipelines stay thin and
focused on their respective prompts and result shapes.
"""

from __future__ import annotations

import logging
import re
import time

from openai import OpenAI

from sift.config import LLMConfig
from sift.pipeline.llm_client import LLMRequestOptions, create_chat_completion
from sift.pipeline.llm_json import log_parse_debug, parse_json_object


# Reasoning models (DeepSeek, OpenCode "v4-pro" variants) may emit their
# chain of thought inside <think>...</think> tags before the final
# answer. Strip those before treating the output as JSON.
_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE)
_OPEN_THINK_RE = re.compile(r"<think\b[^>]*>.*", re.DOTALL | re.IGNORECASE)


REPAIR_JSON_PROMPT = """TASK
Convert this model response into one valid JSON object matching the schema.

SCHEMA
{schema}

MODEL RESPONSE
{raw}

RULES
- Preserve the intended meaning when possible.
- Return only the corrected JSON object."""


class BaseLLMPipeline:
    """Common init / call / parse / warn machinery for LLM pipelines.

    Subclasses supply:
      - ``SYSTEM_PROMPT`` (class attr): the system message sent on every call.
      - ``FALLBACK_NOUN`` (class attr): one word used in the unavailable
        warning, e.g. ``"analysis"`` -> ``"...; using fallback analysis."``.
    """

    SYSTEM_PROMPT: str = ""
    FALLBACK_NOUN: str = "result"

    def __init__(self, config: LLMConfig):
        self.client = None
        self.model = config.model
        self.temperature = config.temperature
        self.max_tokens = config.max_tokens
        self._unavailable_reason = ""
        self._warned_unavailable = False
        self._logger = logging.getLogger(self.__class__.__module__)

        api_key = (config.api_key or "").strip()
        if not api_key:
            self._unavailable_reason = "LLM API key is not configured"
            return

        try:
            self.client = OpenAI(api_key=api_key, base_url=config.base_url)
        except Exception as exc:
            self._unavailable_reason = f"LLM client setup failed: {exc}"

    def _call_llm(self, prompt: str, _retries: int = 2) -> str:
        if self.client is None:
            raise RuntimeError(self._unavailable_reason or "LLM client is unavailable")

        last_reason = "empty response"
        options = LLMRequestOptions(self.temperature, self.max_tokens)
        for attempt in range(_retries + 1):
            if attempt:
                time.sleep(attempt)
            response = create_chat_completion(
                self.client,
                self.model,
                [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                options,
                self._logger,
            )
            choice = response.choices[0]
            message = choice.message
            finish_reason = getattr(choice, "finish_reason", None)

            content = _strip_thinking(message.content or "")
            if content:
                return content

            # Reasoning models often place the final answer in a separate
            # `reasoning_content` field when `content` is empty.
            reasoning = getattr(message, "reasoning_content", None) or ""
            if not reasoning:
                extra = getattr(message, "model_extra", None) or {}
                reasoning = extra.get("reasoning_content") or extra.get("reasoning") or ""
            reasoning = _strip_thinking(reasoning)
            if reasoning:
                self._logger.debug(
                    "LLM content was empty; falling back to reasoning_content (%d chars).",
                    len(reasoning),
                )
                return reasoning

            if finish_reason == "length":
                last_reason = (
                    f"output truncated at max_tokens={self.max_tokens} before any "
                    "visible content was emitted (model spent the budget on reasoning)"
                )
            else:
                last_reason = f"empty response (finish_reason={finish_reason!r})"
            self._logger.debug(
                "LLM returned empty response on attempt %d/%d: %s",
                attempt + 1,
                _retries + 1,
                last_reason,
            )
        raise RuntimeError(f"LLM returned empty response: {last_reason}")

    def _parse_or_repair(self, raw: str, context: str, schema: str) -> dict:
        try:
            return parse_json_object(raw, self._logger, context)
        except Exception as exc:
            log_parse_debug(self._logger, context, raw, exc)

        repair_prompt = REPAIR_JSON_PROMPT.format(schema=schema, raw=raw)
        repaired = self._call_llm(repair_prompt)
        return parse_json_object(repaired, self._logger, f"{context} repair")

    def _warn_unavailable_once(self) -> None:
        if self._warned_unavailable:
            return
        self._logger.warning(
            "%s; using fallback %s.", self._unavailable_reason, self.FALLBACK_NOUN
        )
        self._warned_unavailable = True


def _strip_thinking(text: str) -> str:
    """Remove reasoning-model ``<think>...</think>`` blocks from a response.

    Handles closed blocks and an unclosed trailing block (which happens
    when the response is truncated mid-thought by max_tokens).
    """
    if not text:
        return ""
    cleaned = _THINK_BLOCK_RE.sub("", text)
    cleaned = _OPEN_THINK_RE.sub("", cleaned)
    return cleaned.strip()


def clean_string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def normalize_string_list(value: object, limit: int | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    strings = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return strings[:limit] if limit is not None else strings
