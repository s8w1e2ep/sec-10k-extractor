"""Size-cap and event-loop responsiveness tests.

Two design promises being tested:

1. **Hard 30 MB cap** — pipeline rejects oversized filings *before*
   handing them to BeautifulSoup. A 50 MB blob shouldn't burn 200+ MB
   of memory and 5+ seconds of CPU just to fail the form check after
   the fact.

2. **Async parsing offload** — BeautifulSoup is sync and CPU-bound.
   We push parsing into `asyncio.to_thread()` so the event loop can
   keep serving cached requests / healthz / form-gate rejections in
   parallel with a slow parse.
"""

import asyncio
import time

import pytest

from extractor.pipeline import (
    MAX_RAW_HTML_BYTES,
    OversizedFilingError,
    extract_filing,
)
from extractor.types import FilingMetadata


def _meta(form: str = "10-K") -> FilingMetadata:
    return FilingMetadata(
        cik="0000320193",
        accession_number="0000320193-25-000099",
        form=form,
        filing_date="2025-11-01",
        period_of_report="2025-09-27",
        primary_document_url="https://www.sec.gov/...",
        company_name="Test Co",
    )


@pytest.mark.asyncio
async def test_oversized_filing_rejected_before_parse(monkeypatch):
    """A 31 MB blob should raise OversizedFilingError without ever
    reaching BeautifulSoup. We verify by counting `bs4.BeautifulSoup`
    calls — must be zero."""
    from extractor import pipeline

    async def fake_resolve(cik, accession):
        return _meta()

    too_big = b"<html>" + b"x" * (MAX_RAW_HTML_BYTES + 1024) + b"</html>"

    async def fake_fetch(url):
        return too_big

    parse_calls = []
    real_bs = pipeline.BeautifulSoup

    def counting_bs(*args, **kwargs):
        parse_calls.append(1)
        return real_bs(*args, **kwargs)

    monkeypatch.setattr(pipeline, "resolve_by_cik_accession", fake_resolve)
    monkeypatch.setattr(pipeline, "fetch", fake_fetch)
    monkeypatch.setattr(pipeline, "BeautifulSoup", counting_bs)

    with pytest.raises(OversizedFilingError) as exc_info:
        await extract_filing(cik="320193", accession_number="0000320193-25-000099")

    assert exc_info.value.size_bytes > MAX_RAW_HTML_BYTES
    assert exc_info.value.limit_bytes == MAX_RAW_HTML_BYTES
    assert parse_calls == [], "BeautifulSoup must not be invoked on oversized input"


@pytest.mark.asyncio
async def test_below_cap_is_not_rejected(monkeypatch):
    """A small valid HTML doc should sail through the size gate. The
    rest of the pipeline can fail later — we only assert the size check
    didn't fire prematurely."""
    from extractor import pipeline

    async def fake_resolve(cik, accession):
        return _meta()

    small_html = b"<html><body><p>Item 1. Business</p>" + b"x" * 5000 + b"</body></html>"

    async def fake_fetch(url):
        return small_html

    monkeypatch.setattr(pipeline, "resolve_by_cik_accession", fake_resolve)
    monkeypatch.setattr(pipeline, "fetch", fake_fetch)

    # No OversizedFilingError — pipeline runs through. The result will
    # be sparse (this isn't a real 10-K) but won't crash on the gate.
    result = await extract_filing(cik="320193", accession_number="0000320193-25-000099")
    assert "items" in result
    assert "stats" in result


@pytest.mark.asyncio
async def test_event_loop_responsive_during_parse(monkeypatch):
    """While one extract_filing is busy with BeautifulSoup parsing,
    other coroutines must continue running on the event loop. We
    simulate this by injecting a "slow" parse (200 ms blocking sleep
    inside a sync function) and verify a parallel fast coroutine can
    complete in roughly its own time, not stacked behind the parse."""
    from extractor import pipeline
    from extractor.types import NormalizedDoc

    async def fake_resolve(cik, accession):
        return _meta()

    async def fake_fetch(url):
        return b"<html><body><p>x</p></body></html>"

    def slow_parse_and_normalize(raw, fmt):
        # Simulate 200 ms of blocking CPU work — exactly the kind of
        # thing we want to keep OFF the event loop.
        time.sleep(0.2)
        return None, NormalizedDoc(text="", headings=[], anchors=[], format="html_modern")

    monkeypatch.setattr(pipeline, "resolve_by_cik_accession", fake_resolve)
    monkeypatch.setattr(pipeline, "fetch", fake_fetch)
    monkeypatch.setattr(pipeline, "_parse_and_normalize", slow_parse_and_normalize)

    async def fast_concurrent_work() -> float:
        """A "fast" coroutine that doesn't touch the heavy path. If
        the event loop is free, it should complete in ~50 ms."""
        start = time.monotonic()
        await asyncio.sleep(0.05)
        return time.monotonic() - start

    extract_task = asyncio.create_task(
        extract_filing(cik="320193", accession_number="0000320193-25-000099")
    )
    fast_elapsed = await fast_concurrent_work()
    await extract_task

    # If parsing blocked the event loop, fast_concurrent_work() couldn't
    # have finished in 50 ms — it would be queued behind the 200 ms
    # parse. Allow generous slack (100 ms) for CI jitter; anything much
    # higher means the offload didn't take.
    assert fast_elapsed < 0.10, (
        f"Concurrent coroutine took {fast_elapsed*1000:.0f} ms — event loop "
        f"was blocked by sync parse despite asyncio.to_thread offload"
    )
