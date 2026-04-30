"""Item locator strategies.

- toc_anchor: find <a href="#xxx">Item N. Title</a> entries, look up the target
  anchor's offset in the normalized doc.
- heading_regex: match 'Item N.' patterns against the doc's headings, drop
  out-of-canonical-order matches (TOC echoes / in-body cross-references).
- combine_strategies: merge results, TOC wins on disagreement >200 chars.
"""

import re

from bs4 import BeautifulSoup

from .canonical_items import canonical_index, get_canonical_item, part_sort_key
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


_HEADING_TEXT_RE = re.compile(
    r"^\s*(?:Item|ITEM)\s+(\d{1,2})\s*([A-Z]?)\b\.?\s*[—–:.\-]?\s*(.{0,200})$",
    re.IGNORECASE,
)


def locate_by_heading_regex(doc: NormalizedDoc) -> list[ItemSpan]:
    """Find item starts by matching 'Item N.' on heading text.

    Filters out-of-canonical-order matches (TOC echoes / in-body refs) by
    walking matches in offset order and keeping only those whose canonical
    index is greater than the previously kept one.
    """
    raw_matches: list[tuple[str, int]] = []
    for h in doc.headings:
        m = _HEADING_TEXT_RE.match(h.text.strip())
        if not m:
            continue
        num = m.group(1) + m.group(2).upper()
        if canonical_index(num) is None:
            continue
        raw_matches.append((num, h.char_offset))

    raw_matches.sort(key=lambda t: t[1])

    kept: list[tuple[str, int]] = []
    last_idx = -1
    for num, offset in raw_matches:
        idx = canonical_index(num)
        if idx is None:
            continue
        if idx <= last_idx:
            continue  # out of order — TOC echo or in-body reference
        kept.append((num, offset))
        last_idx = idx

    spans: list[ItemSpan] = []
    for i, (num, start) in enumerate(kept):
        end = kept[i + 1][1] if i + 1 < len(kept) else len(doc.text)
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
                resolved_by="heading",
            )
        )
    spans.sort(key=lambda s: part_sort_key(s.part, s.item_number))
    return spans


def combine_strategies(
    toc_spans: list[ItemSpan],
    heading_spans: list[ItemSpan],
    *,
    disagreement_threshold: int = 200,
    doc_length: int | None = None,
) -> tuple[list[ItemSpan], list[dict]]:
    """Merge TOC and heading-regex results.

    On disagreement >threshold chars, TOC wins (filer's own anchors are more
    reliable than in-body heading text) and a warning is emitted. Heading
    regex fills items TOC missed.
    """
    by_num: dict[str, ItemSpan] = {}
    warnings: list[dict] = []

    for s in toc_spans:
        by_num[s.item_number] = s

    for h in heading_spans:
        existing = by_num.get(h.item_number)
        if existing is None:
            by_num[h.item_number] = h
            continue
        delta = abs(existing.start - h.start)
        if delta > disagreement_threshold:
            warnings.append({
                "code": "strategy_disagreement",
                "message": (
                    f"Item {h.item_number}: toc start={existing.start}, "
                    f"heading start={h.start}, delta={delta}; toc wins."
                ),
                "item": h.item_number,
                "toc_start": existing.start,
                "heading_start": h.start,
                "delta": delta,
            })

    spans = sorted(by_num.values(), key=lambda s: part_sort_key(s.part, s.item_number))
    for i, s in enumerate(spans):
        if i + 1 < len(spans):
            s.end = spans[i + 1].start
        elif doc_length is not None:
            s.end = doc_length
    return spans, warnings
