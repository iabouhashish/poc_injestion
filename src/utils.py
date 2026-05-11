"""
Shared utilities: JSON extraction and retry-with-backoff.
Imported by both pipeline.py and evaluator.py to avoid duplicated logic.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Callable, TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)


def extract_json(raw: str) -> str:
    """
    Extract a JSON object from text that may contain markdown fences or prose.
    Returns the raw string unchanged if no JSON block is detected.
    """
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        return fenced.group(1)
    brace_match = re.search(r"(\{.*\})", raw, re.DOTALL)
    if brace_match:
        return brace_match.group(1)
    return raw


def retry_with_backoff(
    fn: Callable[[], T],
    max_retries: int,
    backoff_base: float,
    context_label: str = "LLM",
) -> T:
    """
    Call fn() with exponential-backoff retry. Raises the last exception if all
    attempts are exhausted.
    """
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            wait = backoff_base ** attempt
            logger.warning(
                "[%s] Attempt %d/%d failed: %s — retrying in %.0fs",
                context_label,
                attempt,
                max_retries,
                exc,
                wait,
            )
            if attempt == max_retries:
                raise
            time.sleep(wait)
    raise RuntimeError(f"{context_label}: exhausted all {max_retries} retry attempts")
