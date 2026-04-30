"""Resolve (CIK, accession) or file_url to a primary 10-K document URL + metadata."""

import json
import re

from .fetcher import fetch
from .types import FilingMetadata


_ACCESSION_DASHED_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_ACCESSION_NODASHES_RE = re.compile(r"^\d{18}$")


def _normalize_cik(cik: str | int) -> str:
    s = str(cik).strip().lstrip("0")
    if not s:
        s = "0"
    if not s.isdigit():
        raise ValueError(f"Invalid CIK: {cik!r}")
    return s.zfill(10)


def _normalize_accession(acc: str) -> str:
    s = acc.strip()
    if _ACCESSION_NODASHES_RE.match(s):
        s = f"{s[:10]}-{s[10:12]}-{s[12:]}"
    if not _ACCESSION_DASHED_RE.match(s):
        raise ValueError(f"Invalid accession number: {acc!r}")
    return s


async def _find_in_submissions(
    data: dict, accession_dashed: str
) -> tuple[int, dict] | None:
    """Search the submissions JSON for a row matching accession_dashed.

    Returns (index, dict-of-arrays) or None.
    """
    recent = data.get("filings", {}).get("recent", {})
    arrs = recent
    accs = arrs.get("accessionNumber", [])
    for i, a in enumerate(accs):
        if a == accession_dashed:
            return i, arrs

    for f in data.get("filings", {}).get("files", []):
        files_url = f"https://data.sec.gov/submissions/{f['name']}"
        raw = await fetch(files_url)
        old = json.loads(raw)
        accs = old.get("accessionNumber", [])
        for i, a in enumerate(accs):
            if a == accession_dashed:
                return i, old
    return None


async def resolve_by_cik_accession(
    cik: str | int, accession: str
) -> FilingMetadata:
    cik_padded = _normalize_cik(cik)
    accession_dashed = _normalize_accession(accession)

    submissions_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    raw = await fetch(submissions_url)
    data = json.loads(raw)

    found = await _find_in_submissions(data, accession_dashed)
    if found is None:
        raise ValueError(
            f"Accession {accession_dashed} not found for CIK {cik_padded}"
        )

    idx, arrs = found
    form = arrs.get("form", [])[idx]
    primary_doc = arrs.get("primaryDocument", [])[idx]
    filing_date = arrs.get("filingDate", [])[idx]
    periods = arrs.get("reportDate", [])
    period = periods[idx] if idx < len(periods) else None

    company_name = data.get("name") or data.get("entityName", "")

    accession_no_dashes = accession_dashed.replace("-", "")
    cik_int = int(cik_padded)
    primary_url = (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{cik_int}/{accession_no_dashes}/{primary_doc}"
    )

    return FilingMetadata(
        cik=cik_padded,
        accession_number=accession_dashed,
        form=form,
        filing_date=filing_date,
        period_of_report=period or None,
        primary_document_url=primary_url,
        company_name=company_name,
    )


_NEW_URL_RE = re.compile(
    r"https?://www\.sec\.gov/Archives/edgar/data/(\d+)/(\d{18})/(.+)$"
)
_OLD_URL_RE = re.compile(
    r"https?://www\.sec\.gov/Archives/edgar/data/(\d+)/(\d{10}-\d{2}-\d{6})\.txt$"
)


async def resolve_by_file_url(file_url: str) -> FilingMetadata:
    """Parse CIK + accession from an EDGAR Archives URL.

    Supports both new-style (post-2002) `/data/{cik}/{acc_no_dashes}/{file}`
    and old-style `/data/{cik}/{acc_dashed}.txt` (full-submission .txt).
    Enriches metadata via Submissions API when available.
    """
    m = _NEW_URL_RE.match(file_url)
    if m:
        cik_int_str = m.group(1)
        acc_nodashes = m.group(2)
        accession_dashed = (
            f"{acc_nodashes[:10]}-{acc_nodashes[10:12]}-{acc_nodashes[12:]}"
        )
    else:
        m = _OLD_URL_RE.match(file_url)
        if not m:
            raise ValueError(f"Could not parse CIK/accession from URL: {file_url!r}")
        cik_int_str = m.group(1)
        accession_dashed = m.group(2)

    cik_padded = _normalize_cik(cik_int_str)

    try:
        meta = await resolve_by_cik_accession(cik_int_str, accession_dashed)
    except Exception:
        return FilingMetadata(
            cik=cik_padded,
            accession_number=accession_dashed,
            form="UNKNOWN",
            filing_date="",
            period_of_report=None,
            primary_document_url=file_url,
            company_name="",
        )
    # If caller passed a specific URL, prefer it over the Submissions-API-derived one.
    return FilingMetadata(
        cik=meta.cik,
        accession_number=meta.accession_number,
        form=meta.form,
        filing_date=meta.filing_date,
        period_of_report=meta.period_of_report,
        primary_document_url=file_url,
        company_name=meta.company_name,
    )
