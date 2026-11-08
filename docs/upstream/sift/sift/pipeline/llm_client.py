import logging
from typing import Any

from openai import BadRequestError


SAFE_MAX_TOKENS_AFTER_400 = 8000


class LLMRequestOptions:
    def __init__(self, temperature: float, max_tokens: int):
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._temperature_enabled = True
        self._max_tokens_enabled = True
        self._token_cap_reduced = False

    def to_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self._temperature_enabled:
            kwargs["temperature"] = self.temperature
        if self._max_tokens_enabled:
            kwargs["max_tokens"] = self.max_tokens
        return kwargs

    def adapt_after_bad_request(self, exc: BadRequestError, logger: logging.Logger) -> bool:
        detail = _bad_request_detail(exc)

        if self._max_tokens_enabled and _mentions_token_limit(detail):
            if self.max_tokens > SAFE_MAX_TOKENS_AFTER_400 and not self._token_cap_reduced:
                logger.warning(
                    "LLM provider rejected max_tokens=%d; retrying once with max_tokens=%d.",
                    self.max_tokens,
                    SAFE_MAX_TOKENS_AFTER_400,
                )
                self.max_tokens = SAFE_MAX_TOKENS_AFTER_400
                self._token_cap_reduced = True
                return True

            logger.warning("LLM provider rejected max_tokens; retrying once without max_tokens.")
            self._max_tokens_enabled = False
            return True

        if self._temperature_enabled and _mentions_temperature(detail):
            logger.warning("LLM provider rejected temperature; retrying once without temperature.")
            self._temperature_enabled = False
            return True

        return False


def create_chat_completion(client, model: str, messages: list[dict[str, str]], options: LLMRequestOptions, logger: logging.Logger):
    while True:
        try:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                **options.to_kwargs(),
            )
        except BadRequestError as exc:
            if not options.adapt_after_bad_request(exc, logger):
                raise


def _bad_request_detail(exc: BadRequestError) -> str:
    body = getattr(exc, "body", None)
    return f"{exc} {body}".lower()


def _mentions_token_limit(detail: str) -> bool:
    token_terms = (
        "max_tokens",
        "max token",
        "maximum token",
        "output token",
        "completion token",
        "too many tokens",
    )
    return any(term in detail for term in token_terms)


def _mentions_temperature(detail: str) -> bool:
    return "temperature" in detail
