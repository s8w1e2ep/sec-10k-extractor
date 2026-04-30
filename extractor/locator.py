"""Item locator strategies.

Phase 1: TOC anchor only — find <a href="#xxx">Item N. Title</a> entries
in the TOC, look up their target anchor's offset in the normalized doc,
and use those offsets as item starts.
"""

import re

from bs4 import BeautifulSoup

from .canonical_items import get_canonical_item, part_sort_key
from .types import ItemSpan, NormalizedDoc


_ITEM_LINK_TEXT_RE = re.compile(
    r"^\s*item\s*(\d{1,2})\s*([a-z]?)\b\.?\s*[—–:.\-]?\s*(.*)$",
    re.IGNORECASE,
)


def _extract_toc_entries(soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    """Return list of (item_number, inline_title, anchor_name) from TOC links."""
    entries: list[tuple[str, str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not isinstance(href, str) or not href.startswith("#"):
            continue
        text = a.get_text(" ", strip=True)
        m = _ITEM_LINK_TEXT_RE.match(text)
        if not m:
            continue
        num = m.group(1) + m.group(2).upper()
        title_inline = m.group(3).strip()
        anchor_name = href[1:]
        if not anchor_name:
            continue
        entries.append((num, title_inline, anchor_name))
    return entries


def locate_by_toc_anchor(
    soup: BeautifulSoup, doc: NormalizedDoc
) -> list[ItemSpan]:
    anchor_offset = {a.name: a.char_offset for a in doc.anchors}

    entries = _extract_toc_entries(soup)
    located: list[tuple[str, str, int]] = []
    seen_items: set[str] = set()
    for num, title, anchor_name in entries:
        if num in seen_items:
            continue
        offset = anchor_offset.get(anchor_name)
        if offset is None:
            continue
        seen_items.add(num)
        located.append((num, title, offset))

    located.sort(key=lambda t: t[2])

    spans: list[ItemSpan] = []
    for i, (num, title, start) in enumerate(located):
        end = located[i + 1][2] if i + 1 < len(located) else len(doc.text)
        canonical = get_canonical_item(num)
        if not canonical:
            continue
        spans.append(
            ItemSpan(
                part=canonical.part,
                item_number=canonical.item_number,
                item_title=canonical.title,
                start=start,
                end=end,
                resolved_by="toc",
            )
        )

    spans.sort(key=lambda s: part_sort_key(s.part, s.item_number))
    return spans
