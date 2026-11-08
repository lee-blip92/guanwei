import json
import logging
from typing import Any


def parse_json_object(raw: str, logger: logging.Logger, context: str) -> dict[str, Any]:
    """Parse the first JSON object from an LLM response.

    Some OpenAI-compatible endpoints wrap valid JSON in prose or markdown
    fences. This keeps Sift strict about the final shape while being tolerant
    of harmless response wrapping.
    """
    cleaned = _strip_code_fence((raw or "").strip())
    if not cleaned:
        raise json.JSONDecodeError("Empty LLM response", cleaned, 0)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(_extract_first_object(cleaned))
        except json.JSONDecodeError:
            # Last resort: try to close a truncated JSON object (common with
            # token-limited responses from deepseek / OpenCode-style APIs).
            parsed = json.loads(_close_truncated(cleaned))

    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object for {context}, got {type(parsed).__name__}")
    return parsed


def _strip_code_fence(text: str) -> str:
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if lines:
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_first_object(text: str) -> str:
    start = text.find("{")
    if start == -1:
        raise json.JSONDecodeError("No JSON object found", text, 0)

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]

    raise json.JSONDecodeError("Unclosed JSON object", text, start)


def _close_truncated(text: str) -> str:
    """Append missing closing characters to a JSON object truncated mid-stream.

    Handles the common deepseek/OpenCode case where the model's output is cut
    off inside a string value, leaving the object unclosed.
    """
    start = text.find("{")
    if start == -1:
        raise json.JSONDecodeError("No JSON object found", text, 0)

    body = text[start:]
    depth = 0
    in_string = False
    escaped = False

    for char in body:
        if escaped:
            escaped = False
            continue
        if in_string:
            if char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1

    suffix = ""
    if in_string:
        suffix += '"'
    if depth > 0:
        suffix += "}" * depth

    if not suffix:
        raise json.JSONDecodeError("Unclosed JSON object", body, 0)

    return body + suffix


def log_parse_debug(
    logger: logging.Logger,
    context: str,
    raw: str,
    exc: Exception,
    max_chars: int = 300,
) -> None:
    preview = " ".join((raw or "").split())[:max_chars]
    logger.debug(
        "Could not parse LLM response for %s (%s: %s). Raw preview: %r",
        context,
        type(exc).__name__,
        exc,
        preview,
    )
