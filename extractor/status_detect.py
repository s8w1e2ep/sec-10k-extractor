"""Per-item status classifier. Pure rules, no LLM.

Length thresholds (200/300/1500) are tuned to status indicators being
formulaic short text. They prevent false positives where the trigger words
appear inside genuine content. See spec §4.4 for the contract.
"""

import re

from .types import Status


_RESERVED_RE = re.compile(r"\[?\s*reserved\s*\]?", re.IGNORECASE)
_NOT_APPLICABLE_RE = re.compile(
    # "Not applicable" canonical / "None applicable" (Moderna typo) / bare
    # "None" or "None." on its own line. The "none applicable" branch
    # catches a real-world variant — drop it and Moderna FY 2025 Item 9C
    # silently falls through to extracted.
    r"\bnot\s+applicable\b"
    r"|\bnone\s+applicable\b"
    r"|(?:^|\n)\s*none\s*\.?\s*(?:\n|$)",
    re.IGNORECASE,
)
# "incorporated by reference" / "incorporated herein by reference" /
# "is incorporated by reference from..." — the optional middle word
# captures "herein" / "in" / "hereby" without requiring it. Earlier
# version used `(?:here)?in?` which silently required at least an "i"
# before "by", so it missed every filer who wrote "incorporated by
# reference" without the "herein" qualifier (BRK, NVDA, JPM, most banks).
_INCORPORATED_RE = re.compile(
    r"incorporat\w+(?:\s+\w+)?\s+by\s+reference",
    re.IGNORECASE,
)
# Compound IBR: filers like JPMorgan put the master IBR statement in
# Item 10 and Items 11-14 just say "Refer to Item 10." Detecting this
# cross-reference lets us classify those items as IBR even though they
# don't carry the canonical "incorporated by reference" phrase.
_CROSS_REF_RE = re.compile(
    r"(?:\brefer\s+to|\bsee)\s+(?:our\s+|the\s+)?item\s+\d{1,2}",
    re.IGNORECASE,
)

# Items 10-14 are the canonical IBR-eligible ones per Form 10-K General
# Instructions G(3) — filers may incorporate them from a DEF 14A proxy
# statement filed within 120 days. We use three signals (in order):
#   1. direct IBR phrase anywhere in the section,
#   2. "Refer to Item N" cross-reference (compound IBR),
#   3. very short content (< 200 chars) — almost always a fragment of a
#      shared-anchor IBR header (Kura Sushi-style "Items 11-14" pointing
#      to a single anchor; the rules locator gives each item just the
#      8-char "PART III\n" preamble).
_IBR_ELIGIBLE_ITEMS = frozenset({"10", "11", "12", "13", "14"})


def detect_status(item_number: str, content_text: str) -> Status:
    text = content_text.strip()
    head_500 = text[:500]
    head_1500 = text[:1500]

    if len(text) < 200 and _RESERVED_RE.search(head_1500):
        return "reserved"

    # Cap raised to 500 chars: NVDA FY 2026 Item 9C is 427 chars because
    # the rules locator includes a trailing "Part III ... will file proxy"
    # preamble that bleeds into the next section. The actual answer is
    # "Not Applicable." in line 2; raising the cap lets that win over the
    # later IBR phrase the preamble accidentally introduces.
    if len(text) < 500 and _NOT_APPLICABLE_RE.search(head_1500):
        return "not_applicable"

    if item_number in _IBR_ELIGIBLE_ITEMS:
        # 1. Direct IBR phrase anywhere in the section.
        if _INCORPORATED_RE.search(text):
            return "incorporated_by_reference"
        # 2. Compound IBR via cross-reference ("Refer to Item 10") — only
        # consider the head, otherwise we'd false-positive on substantive
        # sections that mention another item later in the prose.
        if _CROSS_REF_RE.search(head_500):
            return "incorporated_by_reference"
        # 3. Very short content in an IBR-eligible item — almost always a
        # shared-anchor fragment, not real extracted prose.
        if len(text) < 200:
            return "incorporated_by_reference"
    elif _INCORPORATED_RE.search(head_500) and len(text) < 1500:
        return "incorporated_by_reference"

    return "extracted"
