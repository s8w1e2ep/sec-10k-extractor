"""Top-level pipeline: input → resolve → fetch → normalize → locate → classify."""

import asyncio
import time
import warnings
from datetime import date
from typing import Any

import httpx
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


# Hard cap on raw filing size. The largest realistic 10-K we've seen is
# ~15 MB (JPM with full Industry Guide 3 disclosures); 30 MB leaves
# generous headroom while protecting the single-worker server from a
# pathological filing that would burn 200+ MB of resident memory during
# BeautifulSoup parsing and block the event loop for the entire parse.
MAX_RAW_HTML_BYTES = 30 * 1024 * 1024

from .canonical_items import expected_items_for_period, part_sort_key
from .fetcher import fetch
from .format_detect import detect_format
from .llm_client import (
    MAX_LLM_CALLS_PER_REQUEST,
    LLMUsage,
    daily_budget_remaining,
    is_configured,
)
from .llm_resolver import fallback_locator, resolve_statuses
from .locator import (
    combine_strategies,
    locate_by_heading_regex,
    locate_by_toc_anchor,
)
from .logging_config import get_logger, log_event
from .normalizer import (
    normalize_html,
    normalize_plain_text,
    trim_leading_boilerplate,
)
from .resolver import resolve_by_cik_accession, resolve_by_file_url
from .status_detect import detect_status
from .types import ExtractedItem, FilingMetadata, ItemSpan, NormalizedDoc
from .validator import validate

LOG = get_logger("extractor.pipeline")


class FilingNotFoundError(Exception):
    """SEC EDGAR returned 404 for the requested CIK / accession / file URL.

    Distinguishes "user gave us a non-existent identifier" (404 → user
    fixes input) from "our service crashed" (500 → we fix the bug).
    Server maps to HTTP 404.
    """

    def __init__(self, what: str, where: str):
        self.what = what
        self.where = where
        super().__init__(f"{what} not found at {where}")


class UpstreamError(Exception):
    """SEC EDGAR or related API returned a non-404 error (5xx, network
    problem, retry exhaustion, ...). Not the user's fault — server maps
    to HTTP 502 so the user knows to retry rather than fix their input.
    """

    def __init__(self, status: int, where: str, message: str = ""):
        self.status = status
        self.where = where
        full = f"Upstream {where} failed (status={status})"
        if message:
            full += f": {message}"
        super().__init__(full)


class OversizedFilingError(Exception):
    """Raised when the raw filing exceeds MAX_RAW_HTML_BYTES.

    Parsing a >30 MB HTML doc with BeautifulSoup would burn several
    hundred MB of resident memory and block the event loop for several
    seconds. The single-worker server (rate-limit-required) cannot
    afford that — every other in-flight request would queue. Reject
    early with HTTP 413 instead.
    """

    def __init__(self, size_bytes: int, limit_bytes: int = MAX_RAW_HTML_BYTES):
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"Filing exceeds size limit: "
            f"{size_bytes / 1024 / 1024:.1f} MB > "
            f"{limit_bytes / 1024 / 1024:.1f} MB"
        )


class UnsupportedFormError(Exception):
    """Raised when a non-10-K filing (e.g. 10-K/A, 10-Q, 8-K) is submitted.

    The service contract is "10-K only". 10-K family variants such as
    10-KSB / 10-K405 / 10-KT (legacy small-business and transition-period
    forms) are accepted because they share the same item catalog. Any
    amendment form (`/A` suffix) is explicitly rejected — amendments
    have a different item structure (often partial) that this pipeline
    is not built to handle.
    """

    def __init__(self, form: str):
        self.form = form
        super().__init__(
            f"Unsupported form: {form}. This service supports 10-K only."
        )


def _is_supported_form(form: str) -> bool:
    f = (form or "").strip().upper()
    if not f.startswith("10-K"):
        return False
    if "/A" in f:  # amendments: 10-K/A, 10-KSB/A, 10-KT/A, etc.
        return False
    return True


async def extract_filing(
    *,
    cik: str | None = None,
    accession_number: str | None = None,
    file_url: str | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    log_event(
        LOG,
        "pipeline.start",
        input_kind="file_url" if file_url else "cik_accession",
        cik=cik,
        accession_number=accession_number,
        file_url=file_url,
    )

    if file_url:
        meta = await resolve_by_file_url(file_url)
    elif cik and accession_number:
        meta = await resolve_by_cik_accession(cik, accession_number)
    else:
        raise ValueError("Provide (cik, accession_number) or file_url")

    if meta.form and meta.form != "UNKNOWN" and not _is_supported_form(meta.form):
        raise UnsupportedFormError(meta.form)

    t_fetch = time.monotonic()
    try:
        raw = await fetch(meta.primary_document_url)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise FilingNotFoundError(
                meta.primary_document_url, "SEC document store"
            ) from e
        raise UpstreamError(
            e.response.status_code, "SEC document store"
        ) from e
    except RuntimeError as e:
        # fetcher exhausted retries on 5xx/429
        raise UpstreamError(0, "SEC document store", str(e)) from e
    fetch_ms = int((time.monotonic() - t_fetch) * 1000)

    if len(raw) > MAX_RAW_HTML_BYTES:
        raise OversizedFilingError(len(raw))

    fmt = detect_format(raw, url=meta.primary_document_url)

    warnings_out: list[dict] = []
    usage = LLMUsage()

    # Offload BeautifulSoup parsing + tree walks to a worker thread so
    # the event loop stays responsive during the 1–5 s of CPU-bound work
    # on large filings. lxml releases the GIL during parsing, so multiple
    # concurrent /extract calls can actually parallelise on multi-core.
    soup, doc = await asyncio.to_thread(_parse_and_normalize, raw, fmt)

    toc_spans = locate_by_toc_anchor(soup, doc) if soup is not None else []
    heading_spans = locate_by_heading_regex(doc)
    spans, locator_warnings = combine_strategies(
        toc_spans, heading_spans, doc_length=len(doc.text)
    )
    warnings_out.extend(locator_warnings)

    period_date = _parse_date(meta.period_of_report)
    expected = expected_items_for_period(period_date)
    required = expected_items_for_period(period_date, only_required=True)
    expected_required = {c.item_number for c in required}

    # Layer 2 — locator fallback. Fires when required items are missing AND
    # an API key is configured AND the per-request LLM budget is not yet
    # exhausted. Layer 1 may still fire afterwards in the same request.
    located_now = {s.item_number for s in spans}
    missing_now = sorted(
        expected_required - located_now,
        key=lambda n: part_sort_key("I", n),
    )
    if (
        missing_now
        and is_configured()
        and usage.calls < MAX_LLM_CALLS_PER_REQUEST
    ):
        if daily_budget_remaining() <= 0:
            warnings_out.append({
                "code": "llm_skipped_daily_budget_exhausted",
                "message": (
                    "Locator fallback skipped: daily LLM budget "
                    "exhausted. Filing returned with rules-only coverage."
                ),
                "layer": "locator_fallback",
            })
        else:
            located_items_so_far = _spans_to_items_lite(spans, doc)
            new_spans, l2_warnings = await fallback_locator(
                doc, located_items_so_far, missing_now, usage=usage
            )
            warnings_out.extend(l2_warnings)
            if new_spans:
                spans = _merge_and_recompute(spans + new_spans, len(doc.text))

    items: list[ExtractedItem] = []
    for span in spans:
        raw_content = doc.text[span.start:span.end]
        # Trim leading repeating-page-header artefacts ("Table of Contents",
        # "Parts II and III", page numbers) so content_text is the real section
        # body and char_range.start lines up with it. Coverage may drop slightly
        # because the trimmed boilerplate belongs to no item — that's accurate.
        skip = trim_leading_boilerplate(raw_content)
        new_start = span.start + skip
        content = doc.text[new_start:span.end].strip()
        status = detect_status(span.item_number, content)
        items.append(
            ExtractedItem(
                part=span.part,
                item_number=span.item_number,
                item_title=span.item_title,
                content_text=content,
                char_range_start=new_start,
                char_range_end=span.end,
                status=status,
                resolved_by=span.resolved_by,
            )
        )

    items.sort(key=lambda it: part_sort_key(it.part, it.item_number))

    found_numbers = {it.item_number for it in items}
    missing_numbers = sorted(
        expected_required - found_numbers,
        key=lambda n: part_sort_key("I", n),
    )
    if missing_numbers:
        warnings_out.append({
            "code": "items_missing",
            "message": f"{len(missing_numbers)} canonical items not located",
            "missing": missing_numbers,
        })

    validator_warnings = await validate(items, len(doc.text), meta)
    warnings_out.extend(validator_warnings)

    # Layer 1 — status resolver. Fires when validator flagged title_mismatch
    # on extracted items AND the per-request LLM budget is not exhausted.
    # With MAX_LLM_CALLS_PER_REQUEST > 1 this can run after Layer 2.
    if is_configured() and usage.calls < MAX_LLM_CALLS_PER_REQUEST:
        title_mismatches = [
            w for w in validator_warnings
            if w.get("code") == "title_mismatch"
        ]
        if title_mismatches:
            if daily_budget_remaining() <= 0:
                warnings_out.append({
                    "code": "llm_skipped_daily_budget_exhausted",
                    "message": (
                        "Status resolver skipped: daily LLM budget "
                        "exhausted. Statuses returned as detected by rules."
                    ),
                    "layer": "status_resolver",
                })
            else:
                items, l1_warnings = await resolve_statuses(
                    items, title_mismatches, usage=usage
                )
                warnings_out.extend(l1_warnings)

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
        "llm_calls": usage.calls,
        "llm_input_tokens": usage.input_tokens,
        "llm_output_tokens": usage.output_tokens,
        "estimated_cost_usd": round(usage.cost_usd, 6),
    }

    log_event(
        LOG,
        "pipeline.done",
        cik=meta.cik,
        accession_number=meta.accession_number,
        form=meta.form,
        format=fmt,
        items_extracted=stats["items_extracted"],
        items_missing=stats["items_missing"],
        toc=stats["strategies"]["toc"],
        heading=stats["strategies"]["heading"],
        llm=stats["strategies"]["llm"],
        llm_calls=usage.calls,
        warnings_count=len(warnings_out),
        duration_ms=duration_ms,
        fetch_ms=fetch_ms,
    )

    return {
        "filing": _filing_dict(meta),
        "items": [_item_dict(it) for it in items],
        "stats": stats,
        "warnings": warnings_out,
    }


def _parse_and_normalize(
    raw: bytes, fmt: str
) -> tuple[BeautifulSoup | None, NormalizedDoc]:
    """Sync block bundling all CPU-heavy parsing/normalization in one call.

    Designed to be invoked via `await asyncio.to_thread(...)` so the event
    loop stays free to serve healthz / cached requests / form-gate
    rejections while a large filing is being parsed in a worker thread.

    Plain text path doesn't need BeautifulSoup; returns (None, doc).
    HTML path returns (soup, doc) — the locator needs the parsed tree.
    """
    if fmt == "plain_text":
        return None, normalize_plain_text(raw)
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    doc = normalize_html(soup, fmt)
    return soup, doc


def _spans_to_items_lite(
    spans: list[ItemSpan], doc: NormalizedDoc
) -> list[ExtractedItem]:
    """Lightweight ItemSpan→ExtractedItem so Layer 2 can see what's already
    located. We only need item_number + char_range_start; the other fields
    are filled with placeholders."""
    out: list[ExtractedItem] = []
    for s in spans:
        out.append(ExtractedItem(
            part=s.part,
            item_number=s.item_number,
            item_title=s.item_title,
            content_text="",
            char_range_start=s.start,
            char_range_end=s.end,
            status="extracted",
            resolved_by=s.resolved_by,
        ))
    return out


def _merge_and_recompute(
    spans: list[ItemSpan], doc_length: int
) -> list[ItemSpan]:
    """Sort spans by start offset, drop duplicates, recompute end = next.start.

    De-dupe by item_number — earliest start wins on collision (rules-located
    items take precedence over LLM-proposed when both exist for the same num).
    """
    by_num: dict[str, ItemSpan] = {}
    for s in sorted(spans, key=lambda x: (x.start, x.resolved_by != "llm")):
        if s.item_number in by_num:
            existing = by_num[s.item_number]
            if existing.resolved_by != "llm" and s.resolved_by == "llm":
                continue  # keep rules-located
            if existing.resolved_by == "llm" and s.resolved_by != "llm":
                by_num[s.item_number] = s  # prefer rules
            continue
        by_num[s.item_number] = s
    ordered = sorted(by_num.values(), key=lambda x: x.start)
    for i, s in enumerate(ordered):
        s.end = ordered[i + 1].start if i + 1 < len(ordered) else doc_length
    return ordered


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
