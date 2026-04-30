"""Input-validation and upstream-error mapping tests.

Catches the regression where every SEC failure (404 / 5xx / network)
bubbled up as a generic FastAPI 500. The mapping we want:

  user gave us a non-existent CIK / accession / file_url  →  HTTP 404
  SEC returned 5xx, network died, retries exhausted        →  HTTP 502
  malformed CIK / accession / unparseable URL              →  HTTP 400 (existing)
"""

import httpx
import pytest

from extractor.pipeline import (
    FilingNotFoundError,
    UpstreamError,
    extract_filing,
)
from extractor.resolver import _normalize_accession, _normalize_cik
from extractor.types import FilingMetadata


# ---------- pre-existing input shape validation ----------------------------


def test_normalize_cik_rejects_non_digits():
    with pytest.raises(ValueError, match="Invalid CIK"):
        _normalize_cik("abc")


def test_normalize_cik_pads_short_cik():
    assert _normalize_cik("320193") == "0000320193"
    assert _normalize_cik("0000320193") == "0000320193"


def test_normalize_accession_rejects_garbage():
    with pytest.raises(ValueError, match="Invalid accession number"):
        _normalize_accession("not-an-accession")


def test_normalize_accession_accepts_both_forms():
    # 18-digit no-dashes form auto-normalizes to dashed
    assert _normalize_accession("000032019325000079") == "0000320193-25-000079"
    # already-dashed form passes through
    assert _normalize_accession("0000320193-25-000079") == "0000320193-25-000079"


# ---------- 404 from SEC: CIK / accession / document URL -------------------


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        f"{status}", request=httpx.Request("GET", "https://example"),
        response=_FakeResponse(status),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_nonexistent_cik_returns_filing_not_found(monkeypatch):
    """SEC's Submissions API returns 404 for a non-existent CIK. Pipeline
    should raise FilingNotFoundError (which the server maps to HTTP 404),
    NOT bubble up an httpx error as a 500."""
    from extractor import resolver

    async def fake_fetch(url):
        raise _http_status_error(404)

    monkeypatch.setattr(resolver, "fetch", fake_fetch)

    with pytest.raises(FilingNotFoundError) as exc:
        await extract_filing(cik="9999999999", accession_number="0000000000-00-000000")
    assert "9999999999" in exc.value.what
    assert "Submissions" in exc.value.where


@pytest.mark.asyncio
async def test_sec_5xx_returns_upstream_error(monkeypatch):
    """When SEC returns a 5xx the fetcher's retry loop eventually gives
    up with RuntimeError. Pipeline should surface that as UpstreamError
    (server → HTTP 502), not a generic 500."""
    from extractor import resolver

    async def fake_fetch(url):
        raise RuntimeError(
            "Failed to fetch https://data.sec.gov/... after 4 retries (last status=503)"
        )

    monkeypatch.setattr(resolver, "fetch", fake_fetch)

    with pytest.raises(UpstreamError) as exc:
        await extract_filing(cik="320193", accession_number="0000320193-25-000079")
    assert "Submissions" in exc.value.where


@pytest.mark.asyncio
async def test_accession_not_in_submissions_returns_filing_not_found(monkeypatch):
    """CIK exists but accession doesn't match any of the company's
    filings. Pipeline now raises FilingNotFoundError with both fields,
    not a plain ValueError."""
    from extractor import resolver

    async def fake_fetch(url):
        # Minimal valid Submissions response with a different accession
        return (
            b'{"name": "Test Co", "filings": {"recent": {'
            b'"form": ["10-K"], "accessionNumber": ["1111111111-11-111111"], '
            b'"filingDate": ["2025-01-01"], "reportDate": ["2024-12-31"], '
            b'"primaryDocument": ["a.htm"]}, "files": []}}'
        )

    monkeypatch.setattr(resolver, "fetch", fake_fetch)

    with pytest.raises(FilingNotFoundError) as exc:
        await extract_filing(cik="320193", accession_number="2222222222-22-222222")
    assert "2222222222-22-222222" in exc.value.what


@pytest.mark.asyncio
async def test_document_url_404_returns_filing_not_found(monkeypatch):
    """Resolver returns metadata fine, but the primary document URL
    itself 404s (rare but possible). Should still surface as
    FilingNotFoundError, not bubble up as 500."""
    from extractor import pipeline

    async def fake_resolve(cik, accession):
        return FilingMetadata(
            cik="0000320193",
            accession_number="0000320193-25-000079",
            form="10-K",
            filing_date="2025-11-01",
            period_of_report="2025-09-27",
            primary_document_url="https://www.sec.gov/Archives/.../missing.htm",
            company_name="Test Co",
        )

    async def fake_fetch_doc(url):
        raise _http_status_error(404)

    monkeypatch.setattr(pipeline, "resolve_by_cik_accession", fake_resolve)
    monkeypatch.setattr(pipeline, "fetch", fake_fetch_doc)

    with pytest.raises(FilingNotFoundError) as exc:
        await extract_filing(cik="320193", accession_number="0000320193-25-000079")
    assert "missing.htm" in exc.value.what


# ---------- server-layer mapping (using FastAPI TestClient) ----------------


from fastapi.testclient import TestClient

from server.main import app


def test_server_404_for_nonexistent_cik(monkeypatch):
    """End-to-end: hit /extract via TestClient with a fake resolver
    that simulates a 404 from SEC. Server should respond with HTTP 404
    + the structured `{error, what, where}` body — not 500."""
    from extractor import resolver

    async def fake_fetch(url):
        raise _http_status_error(404)

    monkeypatch.setattr(resolver, "fetch", fake_fetch)
    client = TestClient(app)
    r = client.post(
        "/extract",
        json={"cik": "9999999999", "accession_number": "0000000000-00-000000"},
    )
    assert r.status_code == 404
    body = r.json()
    assert "what" in body and "where" in body
    assert "9999999999" in body["what"]


def test_server_502_for_sec_outage(monkeypatch):
    """Fetcher gives up after 5xx retries — server should return 502."""
    from extractor import resolver

    async def fake_fetch(url):
        raise RuntimeError("Failed to fetch ... last status=503")

    monkeypatch.setattr(resolver, "fetch", fake_fetch)
    client = TestClient(app)
    r = client.post(
        "/extract",
        json={"cik": "320193", "accession_number": "0000320193-25-000079"},
    )
    assert r.status_code == 502
    body = r.json()
    assert body["upstream"] == "SEC Submissions API"


def test_server_400_for_malformed_cik():
    client = TestClient(app)
    r = client.post(
        "/extract",
        json={"cik": "abc", "accession_number": "0000320193-25-000079"},
    )
    assert r.status_code == 400


def test_server_422_for_missing_fields():
    """Pydantic validator catches "neither file_url nor (cik+accession)"."""
    client = TestClient(app)
    r = client.post("/extract", json={})
    assert r.status_code == 422


def test_server_422_for_conflicting_inputs():
    """Pydantic validator: can't pass file_url AND cik+accession."""
    client = TestClient(app)
    r = client.post(
        "/extract",
        json={
            "cik": "320193",
            "accession_number": "0000320193-25-000079",
            "file_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl.htm",
        },
    )
    assert r.status_code == 422


# ---------- legacy URL fallback for pre-2001 Submissions API rows -----------


@pytest.mark.asyncio
async def test_resolver_uses_legacy_txt_url_when_primarydocument_empty(monkeypatch):
    """Pre-~2001 filings have an empty `primaryDocument` field in the
    Submissions API. Without a fallback, the resolver builds a URL
    ending in `/` (the accession-folder index) and SEC serves a
    directory-listing HTML — locator finds zero items, every required
    item shows up in `items_missing`. The fix: when `primaryDocument`
    is empty, build the legacy single-.txt URL the SEC stored these
    submissions at. Reproduced live with AAPL FY1996
    (CIK 320193 / 0000320193-96-000023)."""
    import json as _json

    from extractor import resolver

    canned = {
        "name": "Old Co",
        "filings": {
            "recent": {
                "accessionNumber": ["0000123456-99-000001"],
                "form": ["10-K"],
                "filingDate": ["1999-12-31"],
                "reportDate": ["1999-09-30"],
                "primaryDocument": [""],
                "primaryDocDescription": [""],
            },
            "files": [],
        },
    }

    async def fake_fetch(url, **_):
        assert "submissions" in url, f"unexpected fetch: {url}"
        return _json.dumps(canned).encode()

    monkeypatch.setattr(resolver, "fetch", fake_fetch)

    meta = await resolver.resolve_by_cik_accession("123456", "0000123456-99-000001")
    assert meta.primary_document_url == (
        "https://www.sec.gov/Archives/edgar/data/123456/0000123456-99-000001.txt"
    )
    assert not meta.primary_document_url.endswith("/")


@pytest.mark.asyncio
async def test_resolver_uses_per_accession_path_when_primarydocument_present(monkeypatch):
    """Modern (~2001+) filings: `primaryDocument` is the actual
    filename. The URL goes through the per-accession folder. Guards
    against the legacy-fallback fix accidentally triggering here."""
    import json as _json

    from extractor import resolver

    canned = {
        "name": "Modern Co",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-25-000079"],
                "form": ["10-K"],
                "filingDate": ["2025-10-31"],
                "reportDate": ["2025-09-27"],
                "primaryDocument": ["aapl-20250927.htm"],
                "primaryDocDescription": ["10-K"],
            },
            "files": [],
        },
    }

    async def fake_fetch(url, **_):
        assert "submissions" in url, f"unexpected fetch: {url}"
        return _json.dumps(canned).encode()

    monkeypatch.setattr(resolver, "fetch", fake_fetch)

    meta = await resolver.resolve_by_cik_accession("320193", "0000320193-25-000079")
    assert meta.primary_document_url == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019325000079/aapl-20250927.htm"
    )
