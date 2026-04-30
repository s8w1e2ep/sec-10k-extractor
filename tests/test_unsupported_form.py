"""Form-gating tests.

The service contract is "10-K only". Amendments (anything ending in /A)
and non-10-K forms (10-Q, 8-K, 20-F, 40-F, etc.) are rejected before the
pipeline does any heavy work. Historical 10-K family forms (10-KSB,
10-K405, 10-KT) are accepted because they share the same item catalog.
"""

import pytest

from extractor.pipeline import (
    UnsupportedFormError,
    _is_supported_form,
    extract_filing,
)


@pytest.mark.parametrize(
    "form,expected",
    [
        ("10-K", True),
        ("10-K405", True),     # SOX-era variant
        ("10-KSB", True),      # legacy small-business form
        ("10-KSB405", True),
        ("10-KT", True),       # transition-period 10-K
        ("10-K/A", False),     # the canonical case the user called out
        ("10-KSB/A", False),
        ("10-KT/A", False),
        ("10-Q", False),
        ("10-Q/A", False),
        ("8-K", False),
        ("20-F", False),
        ("40-F", False),
        ("DEF 14A", False),
        ("S-1", False),
        ("", False),
        ("10-k", True),        # case-insensitive
        ("10-K ", True),       # trailing whitespace tolerated
    ],
)
def test_is_supported_form(form, expected):
    assert _is_supported_form(form) is expected


@pytest.mark.asyncio
async def test_extract_rejects_10ka_via_resolver(monkeypatch):
    """When (cik, accession) resolves to a 10-K/A, pipeline must raise
    UnsupportedFormError before fetching/parsing the doc."""
    from extractor import pipeline
    from extractor.types import FilingMetadata

    async def fake_resolve(cik, accession):
        return FilingMetadata(
            cik="0000320193",
            accession_number="0000320193-25-000079",
            form="10-K/A",
            filing_date="2025-11-01",
            period_of_report="2025-09-27",
            primary_document_url="https://www.sec.gov/...",
            company_name="Apple Inc.",
        )

    async def fake_fetch(url):
        raise AssertionError("fetch must not be called for unsupported forms")

    monkeypatch.setattr(pipeline, "resolve_by_cik_accession", fake_resolve)
    monkeypatch.setattr(pipeline, "fetch", fake_fetch)

    with pytest.raises(UnsupportedFormError) as exc_info:
        await extract_filing(cik="320193", accession_number="0000320193-25-000079")
    assert exc_info.value.form == "10-K/A"
    assert "10-K only" in str(exc_info.value)


@pytest.mark.asyncio
async def test_extract_rejects_10q(monkeypatch):
    from extractor import pipeline
    from extractor.types import FilingMetadata

    async def fake_resolve(cik, accession):
        return FilingMetadata(
            cik="0000320193",
            accession_number="0000320193-25-000079",
            form="10-Q",
            filing_date="2025-08-01",
            period_of_report="2025-06-30",
            primary_document_url="https://www.sec.gov/...",
            company_name="Apple Inc.",
        )

    async def fake_fetch(url):
        raise AssertionError("fetch must not be called for unsupported forms")

    monkeypatch.setattr(pipeline, "resolve_by_cik_accession", fake_resolve)
    monkeypatch.setattr(pipeline, "fetch", fake_fetch)

    with pytest.raises(UnsupportedFormError) as exc_info:
        await extract_filing(cik="320193", accession_number="0000320193-25-000079")
    assert exc_info.value.form == "10-Q"


@pytest.mark.asyncio
async def test_extract_accepts_10ksb(monkeypatch):
    """10-KSB (legacy small-business) shares the same item catalog as
    10-K and must NOT be rejected. We don't care if the rest of the
    pipeline runs here — only that the form-gate lets it through."""
    from extractor import pipeline
    from extractor.types import FilingMetadata

    async def fake_resolve(cik, accession):
        return FilingMetadata(
            cik="0000123456",
            accession_number="0000123456-08-000001",
            form="10-KSB",
            filing_date="2008-03-15",
            period_of_report="2007-12-31",
            primary_document_url="https://www.sec.gov/fake.htm",
            company_name="Tiny Co",
        )

    class _GateAcceptedSentinel(Exception):
        """Distinct from RuntimeError (which pipeline now catches as
        UpstreamError) so we can prove the form gate let us through."""

    async def fake_fetch(url):
        raise _GateAcceptedSentinel("fetch reached — gate accepted 10-KSB")

    monkeypatch.setattr(pipeline, "resolve_by_cik_accession", fake_resolve)
    monkeypatch.setattr(pipeline, "fetch", fake_fetch)

    with pytest.raises(_GateAcceptedSentinel, match="gate accepted 10-KSB"):
        await extract_filing(cik="123456", accession_number="0000123456-08-000001")


@pytest.mark.asyncio
async def test_extract_skips_form_check_for_unknown_form(monkeypatch):
    """`resolve_by_file_url` falls back to form='UNKNOWN' when the
    Submissions API can't be reached. We let those through — the
    user-supplied URL alone doesn't tell us if it's an amendment, and
    blocking on an unknown form would break the file_url path for
    legitimate 10-Ks where the Submissions API was momentarily down."""
    from extractor import pipeline
    from extractor.types import FilingMetadata

    async def fake_resolve_file(file_url):
        return FilingMetadata(
            cik="0000123456",
            accession_number="0000123456-08-000001",
            form="UNKNOWN",
            filing_date="",
            period_of_report=None,
            primary_document_url=file_url,
            company_name="",
        )

    class _GateAcceptedSentinel(Exception):
        pass

    async def fake_fetch(url):
        raise _GateAcceptedSentinel("fetch reached — gate accepted UNKNOWN")

    monkeypatch.setattr(pipeline, "resolve_by_file_url", fake_resolve_file)
    monkeypatch.setattr(pipeline, "fetch", fake_fetch)

    with pytest.raises(_GateAcceptedSentinel, match="gate accepted UNKNOWN"):
        await extract_filing(file_url="https://www.sec.gov/Archives/edgar/data/123/abc.htm")
