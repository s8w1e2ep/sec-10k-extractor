"""Self-verification checks. Returns a list of warnings; does NOT fail requests.

The job of these checks is to surface things that look wrong without us having
ground truth — disagreements between independent signals (cross-strategy
agreement is recorded by the locator; here we add char-range geometry, brevity
sanity, canonical-title fuzzy match, and XBRL Company Facts cross-check).
"""

import json
import re

import httpx
from rapidfuzz import fuzz

from .canonical_items import canonical_index, get_canonical_item
from .fetcher import fetch
from .types import ExtractedItem, FilingMetadata


_SECTION_HEADING_RE = re.compile(
    r"^\s*(?:Item|ITEM)\s+\d{1,2}[A-Z]?\s*\.?\s*[—–:.\-]?\s*(.+?)\s*$",
    re.IGNORECASE,
)


async def validate(
    items: list[ExtractedItem],
    doc_length: int,
    meta: FilingMetadata,
    *,
    skip_xbrl: bool = False,
) -> list[dict]:
    warnings: list[dict] = []
    warnings.extend(_check_char_range_overlap(items))
    warnings.extend(_check_coverage(items, doc_length))
    warnings.extend(_check_monotonicity(items))
    warnings.extend(_check_brevity_sanity(items))
    warnings.extend(_check_title_match(items))
    if not skip_xbrl:
        warnings.extend(await _check_xbrl_item8(items, meta))
    return warnings


def _check_char_range_overlap(items: list[ExtractedItem]) -> list[dict]:
    out: list[dict] = []
    # Per-item sanity: end must be >= start
    for it in items:
        if it.char_range_end < it.char_range_start:
            out.append({
                "code": "char_range_invalid",
                "message": (
                    f"Item {it.item_number}: end={it.char_range_end} < "
                    f"start={it.char_range_start}; range is invalid"
                ),
                "item": it.item_number,
            })
    # Adjacent overlap (in canonical order)
    for prev, curr in zip(items, items[1:]):
        if prev.char_range_end > curr.char_range_start:
            out.append({
                "code": "char_range_overlap",
                "message": (
                    f"Item {prev.item_number} ends at {prev.char_range_end}, "
                    f"Item {curr.item_number} starts at {curr.char_range_start} "
                    f"(overlap of {prev.char_range_end - curr.char_range_start} chars)"
                ),
                "items": [prev.item_number, curr.item_number],
            })
    return out


def _check_coverage(items: list[ExtractedItem], doc_length: int) -> list[dict]:
    if doc_length == 0:
        return []
    covered = sum(it.char_range_end - it.char_range_start for it in items)
    coverage = covered / doc_length
    if coverage < 0.5:
        return [{
            "code": "low_coverage",
            "message": (
                f"Located items cover {coverage * 100:.1f}% of normalized doc "
                f"({covered}/{doc_length} chars); expected ≥ 50%"
            ),
            "coverage": round(coverage, 3),
            "covered": covered,
            "doc_length": doc_length,
        }]
    return []


def _check_monotonicity(items: list[ExtractedItem]) -> list[dict]:
    out: list[dict] = []
    last_idx = -1
    for it in items:
        idx = canonical_index(it.item_number)
        if idx is None:
            continue
        if idx <= last_idx:
            out.append({
                "code": "non_monotonic_order",
                "message": (
                    f"Item {it.item_number} (canonical index {idx}) appears "
                    f"after a higher-index item"
                ),
                "item": it.item_number,
            })
        else:
            last_idx = idx
    return out


def _check_brevity_sanity(items: list[ExtractedItem]) -> list[dict]:
    out: list[dict] = []
    for it in items:
        if it.item_number == "1" and it.status == "extracted":
            length = it.char_range_end - it.char_range_start
            if length < 1000:
                out.append({
                    "code": "suspect_brevity",
                    "message": (
                        f"Item 1 (Business) extracted but only {length} chars; "
                        "filer may be a small REIT/shell co or extraction is wrong"
                    ),
                    "item": "1",
                    "length": length,
                })
    return out


def _extract_section_heading(content_text: str) -> str:
    text = content_text.strip()
    if not text:
        return ""
    first_line = text.splitlines()[0]
    m = _SECTION_HEADING_RE.match(first_line)
    if m:
        return m.group(1).strip()
    return first_line.strip()


def _check_title_match(
    items: list[ExtractedItem], threshold: int = 75
) -> list[dict]:
    """Compare the heading text inside content_text against the canonical title.

    Catches era-mismatch (e.g. pre-2003 Item 14 = Exhibits but our canonical
    title for Item 14 is now Principal Accountant Fees) and misidentifications.
    Aliases are checked too — Item 4 in pre-2011 filings says "Submission of
    Matters..." which is a known alias of the post-2011 "Mine Safety".

    Skipped for non-extracted statuses since IBR / N/A / reserved sections have
    formulaic non-title content.
    """
    out: list[dict] = []
    for it in items:
        if it.status != "extracted":
            continue
        canonical = get_canonical_item(it.item_number)
        if not canonical:
            continue
        section_heading = _extract_section_heading(it.content_text)
        if not section_heading:
            continue
        head_low = section_heading.lower()
        candidates = [canonical.title.lower()] + [a.lower() for a in canonical.aliases]
        score = max(fuzz.partial_ratio(head_low, c) for c in candidates)
        if score < threshold:
            out.append({
                "code": "title_mismatch",
                "message": (
                    f"Item {it.item_number}: detected '{section_heading[:80]}' "
                    f"vs canonical '{canonical.title[:80]}' (score={int(score)})"
                ),
                "item": it.item_number,
                "detected_title": section_heading[:200],
                "canonical_title": canonical.title,
                "score": int(score),
            })
    return out


async def _check_xbrl_item8(
    items: list[ExtractedItem], meta: FilingMetadata
) -> list[dict]:
    """Item 8 (Financial Statements) extracted ⇒ Company Facts should have data.

    Skipped for pre-2009 filings (XBRL mandate started June 2009). Also skipped
    when Item 8 is IBR / N/A / reserved.
    """
    item8 = next((it for it in items if it.item_number == "8"), None)
    if not item8 or item8.status != "extracted":
        return []

    if meta.period_of_report:
        try:
            year = int(meta.period_of_report[:4])
        except (ValueError, IndexError):
            year = None
        if year is not None and year < 2009:
            return []

    cik_int = int(meta.cik)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_int:010d}.json"
    try:
        raw = await fetch(url)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return [{
                "code": "xbrl_not_filed",
                "message": (
                    f"Item 8 extracted but Company Facts API returned 404 for "
                    f"CIK {meta.cik} — company has not filed XBRL"
                ),
            }]
        return [{
            "code": "xbrl_fetch_error",
            "message": f"Could not fetch Company Facts: HTTP {e.response.status_code}",
        }]
    except Exception as e:
        return [{
            "code": "xbrl_fetch_error",
            "message": f"Could not fetch Company Facts: {e}",
        }]

    try:
        data = json.loads(raw)
    except Exception:
        return [{"code": "xbrl_parse_error", "message": "Company Facts JSON invalid"}]

    us_gaap = data.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        return [{
            "code": "xbrl_no_us_gaap",
            "message": (
                "Item 8 extracted but Company Facts has no us-gaap facts for "
                f"CIK {meta.cik}"
            ),
        }]
    return []
