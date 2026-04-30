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
# Some filers (e.g. Microsoft, Berkshire, Apollo) put the title alone in the
# link text ("Business", "Risk Factors") and encode the item number in the
# href itself ("#item_1_business", "#item_1a_risk_factors", "#item5_market").
# Lookahead requires a separator/non-word/end after the digit-letter group so
# we don't false-match things like "#item5market" as 5M (group 2 grabbing 'm').
_ITEM_HREF_RE = re.compile(
    r"^#item[_\-]?(\d{1,2})([a-z]?)(?=[_\-]|\W|$)",
    re.IGNORECASE,
)


def _extract_toc_entries(soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    """Return list of (item_number, inline_title, anchor_name) from TOC links.

    Three strategies, applied in priority order; first hit wins per item:

    1. **Row-level**: scan `<tr>`/`<li>` rows whose text matches "Item N. ...".
       Handles filers (Apollo, Tesla) where the item number is in one cell
       and the title link is in an adjacent cell, or where "Item 1C." is
       fragmented across multiple anchors so individual link text reads
       "Item 1" + "C".
    2. **Link-text**: AAPL-style — single `<a>` whose own text is "Item N. Title".
    3. **Href fallback**: MSFT/BRK-style — link text is just the title, item
       number is encoded in the href (`#item_1_business`).
    """
    seen: dict[str, tuple[str, str]] = {}  # item_num → (title, anchor)

    def _record(num: str, title: str, anchor: str) -> None:
        if not anchor or num in seen:
            return
        seen[num] = (title, anchor)

    def _first_hash_link(node) -> str | None:
        for a in node.find_all("a", href=True):
            href = a.get("href")
            if isinstance(href, str) and href.startswith("#") and len(href) > 1:
                return href[1:]
        return None

    # 1. Row-level
    for row in soup.find_all(["tr", "li"]):
        text = row.get_text(" ", strip=True)
        if not text:
            continue
        m = _ITEM_LINK_TEXT_RE.match(text)
        if not m:
            continue
        anchor = _first_hash_link(row)
        if not anchor:
            continue
        num = m.group(1) + m.group(2).upper()
        title = m.group(3).strip()
        _record(num, title, anchor)

    # 2. Link-text on individual <a>
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if not isinstance(href, str) or not href.startswith("#") or len(href) <= 1:
            continue
        text = a.get_text(" ", strip=True)
        m = _ITEM_LINK_TEXT_RE.match(text)
        if not m:
            continue
        num = m.group(1) + m.group(2).upper()
        title = m.group(3).strip()
        _record(num, title, href[1:])

    # 3. Href fallback
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if not isinstance(href, str) or not href.startswith("#") or len(href) <= 1:
            continue
        m_href = _ITEM_HREF_RE.match(href)
        if not m_href:
            continue
        num = m_href.group(1) + m_href.group(2).upper()
        title = a.get_text(" ", strip=True)
        _record(num, title, href[1:])

    return [(num, t, anchor) for num, (t, anchor) in seen.items()]


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

    spans = list(by_num.values())

    # End-computation by next-distinct-start offset (not canonical order).
    # Two real-world cases this handles:
    #
    #  1. **Shared TOC anchors** (e.g. Kura Sushi FY 2025): the filer used
    #     one anchor `part_iii_items_11_through_14` for Items 11-14 and a
    #     separate one for Item 10. With canonical ordering, Item 10's end
    #     would be set to Item 11's start — but Item 11's start (288108) is
    #     *earlier* in the doc than Item 10's (288116), producing an
    #     inverted range.
    #  2. **Out-of-order anchors**: occasionally a filer's anchors don't
    #     match canonical order at all (rare, but observed).
    #
    # Offset-based end-setting: each span's end is the next strictly-larger
    # distinct start across all spans, or doc_length if none. Items at the
    # same start get identical (start, end) — the validator suppresses the
    # resulting "overlap" because it's a deliberate filer choice.
    distinct_starts = sorted({s.start for s in spans})
    next_after: dict[int, int] = {}
    for i, off in enumerate(distinct_starts):
        next_after[off] = (
            distinct_starts[i + 1]
            if i + 1 < len(distinct_starts)
            else (doc_length if doc_length is not None else off)
        )
    for s in spans:
        s.end = next_after[s.start]

    spans.sort(key=lambda s: part_sort_key(s.part, s.item_number))
    return spans, warnings
