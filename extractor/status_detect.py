"""Per-item status classifier. Pure rules, no LLM.

Length thresholds (200/300/1500) are tuned to status indicators being
formulaic short text. They prevent false positives where the trigger words
appear inside genuine content. See spec §4.4 for the contract.
"""

import re

from .types import Status


_RESERVED_RE = re.compile(r"\[?\s*reserved\s*\]?", re.IGNORECASE)
_NOT_APPLICABLE_RE = re.compile(
    r"\bnot\s+applicable\b|(?:^|\n)\s*none\s*\.?\s*(?:\n|$)",
    re.IGNORECASE,
)
_INCORPORATED_RE = re.compile(
    r"incorporat\w+\s+(?:here)?in?\s*by\s+reference",
    re.IGNORECASE,
)


def detect_status(item_number: str, content_text: str) -> Status:
    text = content_text.strip()
    head_500 = text[:500]
    head_1500 = text[:1500]

    if len(text) < 200 and _RESERVED_RE.search(head_1500):
        return "reserved"

    if len(text) < 300 and _NOT_APPLICABLE_RE.search(head_1500):
        return "not_applicable"

    if _INCORPORATED_RE.search(head_500) and len(text) < 1500:
        return "incorporated_by_reference"

    return "extracted"
