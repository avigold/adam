"""Robust JSON extraction from LLM text responses.

Multiple strategies for extracting JSON from text that may contain
markdown fences, preamble, wrapping, trailing commas, or truncation
from hitting max_tokens.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def extract_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from LLM response text.

    Tries (in order):
    1. Direct parse
    2. ```json ... ``` fenced block
    3. ``` ... ``` fenced block
    4. First { ... } block
    5. Lenient parse (strip trailing commas)
    6. Truncation repair (close unclosed brackets)

    Returns the parsed dict, or None if all strategies fail.
    """
    candidates: list[str] = []

    # Strategy 1: raw text
    stripped = text.strip()
    candidates.append(stripped)

    # Strategy 2: ```json fenced
    if "```json" in text:
        for block in text.split("```json")[1:]:
            if "```" in block:
                candidates.append(block.split("```")[0].strip())

    # Strategy 3: ``` fenced
    if "```" in text:
        parts = text.split("```")
        for i in range(1, len(parts), 2):
            block = parts[i]
            lines = block.split("\n", 1)
            if len(lines) > 1:
                candidates.append(lines[1].strip())
            candidates.append(block.strip())

    # Strategy 4: first { ... } block
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        candidates.append(brace_match.group(0))

    # Strategy 5: strip trailing commas (common LLM mistake)
    for candidate in list(candidates):
        cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
        if cleaned != candidate:
            candidates.append(cleaned)

    # Try each candidate
    for candidate in candidates:
        if not candidate:
            continue
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            continue

    # Strategy 6: truncation repair — the response was cut off
    # by max_tokens, leaving unclosed brackets
    json_start = text.find("{")
    if json_start >= 0:
        repaired = repair_truncated_json(text[json_start:])
        if repaired is not None:
            return repaired

    return None


def repair_truncated_json(text: str) -> dict[str, Any] | None:
    """Attempt to repair JSON truncated by max_tokens.

    Strategy: strip the last incomplete value, then close all
    open brackets/braces.
    """
    text = text.rstrip()

    # Remove trailing incomplete string (unclosed quote)
    if text.count('"') % 2 != 0:
        last_quote = text.rfind('"')
        text = text[:last_quote + 1]

    # Strip back to last complete value
    while text and text[-1] not in ',]}"0123456789elnsu':
        text = text[:-1]

    # Remove trailing comma
    text = text.rstrip().rstrip(",")

    # Count and close open brackets
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")

    if open_braces < 0 or open_brackets < 0:
        return None  # Malformed beyond repair

    text += "]" * open_brackets + "}" * open_braces

    # Clean trailing commas that might have been exposed
    text = re.sub(r",\s*([}\]])", r"\1", text)

    try:
        result = json.loads(text)
        if isinstance(result, dict):
            logger.info(
                "Repaired truncated JSON (closed %d braces, %d brackets)",
                open_braces, open_brackets,
            )
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    return None
