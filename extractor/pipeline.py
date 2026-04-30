"""Top-level pipeline: input → resolve → fetch → normalize → locate → classify."""

import time
import warnings
from datetime import date
from typing import Any

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from .canonical_items import expected_items_for_period, part_sort_key
from .fetcher import fetch
from .format_detect import detect_format
from .locator import locate_by_toc_anchor
from .normalizer import normalize_html
from .resolver import resolve_by_cik_accession, resolve_by_file_url
from .status_detect import detect_status
from .types import ExtractedItem, FilingMetadata


async def extract_filing(
    *,
    cik: str | None = None,
    accession_number: str | None = None,
    file_url: str | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()

    if file_url:
        meta = await resolve_by_file_url(file_url)
    elif cik and accession_number:
        meta = await resolve_by_cik_accession(cik, accession_number)
    else:
        raise ValueError("Provide (cik, accession_number) or file_url")

    t_fetch = time.monotonic()
    raw = await fetch(meta.primary_document_url)
    fetch_ms = int((time.monotonic() - t_fetch) * 1000)

    fmt = detect_format(raw, url=meta.primary_document_url)

    warnings: list[dict] = []
    items: list[ExtractedItem] = []

    if fmt in ("html_modern", "html_legacy"):
        soup = BeautifulSoup(raw, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        doc = normalize_html(soup, fmt)
        spans = locate_by_toc_anchor(soup, doc)
        for span in spans:
            content = doc.text[span.start:span.end].strip()
            status = detect_status(span.item_number, content)
            items.append(
                ExtractedItem(
                    part=span.part,
                    item_number=span.item_number,
                    item_title=span.item_title,
                    content_text=content,
                    char_range_start=span.start,
                    char_range_end=span.end,
                    status=status,
                    resolved_by=span.resolved_by,
                )
            )
    else:
        warnings.append({
            "code": "format_unsupported",
            "message": f"format={fmt} not supported in Phase 1; deferred to Phase 2",
        })

    items.sort(key=lambda it: part_sort_key(it.part, it.item_number))

    period_date = _parse_date(meta.period_of_report)
    expected = expected_items_for_period(period_date)
    expected_numbers = {c.item_number for c in expected}
    found_numbers = {it.item_number for it in items}
    missing_numbers = sorted(
        expected_numbers - found_numbers,
        key=lambda n: part_sort_key("I", n),
    )
    if missing_numbers:
        warnings.append({
            "code": "items_missing",
            "message": f"{len(missing_numbers)} canonical items not located",
            "missing": missing_numbers,
        })

    duration_ms = int((time.monotonic() - t0) * 1000)
    stats = {
        "items_total": len(expected),
        "items_extracted": sum(1 for it in items if it.status == "extracted"),
        "items_incorporated_by_reference": sum(
            1 for it in items if it.status == "incorporated_by_reference"
        ),
        "items_not_applicable": sum(
            1 for it in items if it.status == "not_applicable"
        ),
        "items_reserved": sum(1 for it in items if it.status == "reserved"),
        "items_missing": len(missing_numbers),
        "strategies": {
            "toc": sum(1 for it in items if it.resolved_by == "toc"),
            "heading": sum(1 for it in items if it.resolved_by == "heading"),
            "llm": sum(1 for it in items if it.resolved_by == "llm"),
        },
        "duration_ms": duration_ms,
        "fetch_ms": fetch_ms,
        "format": fmt,
        "llm_calls": 0,
        "llm_input_tokens": 0,
        "llm_output_tokens": 0,
        "estimated_cost_usd": 0.0,
    }

    return {
        "filing": _filing_dict(meta),
        "items": [_item_dict(it) for it in items],
        "stats": stats,
        "warnings": warnings,
    }


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _filing_dict(meta: FilingMetadata) -> dict:
    return {
        "cik": meta.cik,
        "accession_number": meta.accession_number,
        "form": meta.form,
        "filing_date": meta.filing_date,
        "period_of_report": meta.period_of_report,
        "primary_document_url": meta.primary_document_url,
        "company_name": meta.company_name,
    }


def _item_dict(it: ExtractedItem) -> dict:
    return {
        "part": it.part,
        "item_number": it.item_number,
        "item_title": it.item_title,
        "content_text": it.content_text,
        "char_range": {"start": it.char_range_start, "end": it.char_range_end},
        "status": it.status,
        "resolved_by": it.resolved_by,
    }
