import pytest

from extractor.types import ExtractedItem, FilingMetadata
from extractor.validator import (
    _check_brevity_sanity,
    _check_char_range_overlap,
    _check_coverage,
    _check_monotonicity,
    _check_title_match,
    validate,
)


def _item(num: str, start: int, end: int, *, status="extracted", text=None) -> ExtractedItem:
    if text is None:
        text = f"Item {num}. Section content goes here."
    return ExtractedItem(
        part="I",
        item_number=num,
        item_title=f"Stub Title {num}",
        content_text=text,
        char_range_start=start,
        char_range_end=end,
        status=status,
        resolved_by="toc",
    )


def _meta(period: str | None = "2024-09-28", cik: str = "0000320193") -> FilingMetadata:
    return FilingMetadata(
        cik=cik,
        accession_number="0000320193-24-000123",
        form="10-K",
        filing_date="2024-11-01",
        period_of_report=period,
        primary_document_url="https://www.sec.gov/...",
        company_name="Test Co",
    )


def test_overlap_detected():
    items = [_item("1", 0, 1500), _item("2", 1000, 2000)]
    warns = _check_char_range_overlap(items)
    codes = [w["code"] for w in warns]
    assert "char_range_overlap" in codes


def test_no_overlap_when_adjacent():
    items = [_item("1", 0, 1000), _item("2", 1000, 2000)]
    warns = _check_char_range_overlap(items)
    assert warns == []


def test_invalid_range_detected():
    items = [_item("1", 1000, 500)]
    warns = _check_char_range_overlap(items)
    codes = [w["code"] for w in warns]
    assert "char_range_invalid" in codes


def test_coverage_low_warns():
    items = [_item("1", 0, 100)]
    warns = _check_coverage(items, doc_length=1000)
    assert warns and warns[0]["code"] == "low_coverage"


def test_coverage_high_no_warn():
    items = [_item("1", 0, 800)]
    warns = _check_coverage(items, doc_length=1000)
    assert warns == []


def test_monotonicity_correct():
    items = [_item("1", 0, 100), _item("1A", 100, 200), _item("2", 200, 300)]
    assert _check_monotonicity(items) == []


def test_monotonicity_violation():
    items = [_item("2", 0, 100), _item("1", 100, 200)]
    warns = _check_monotonicity(items)
    assert warns and warns[0]["code"] == "non_monotonic_order"


def test_brevity_item1_too_short():
    short = _item("1", 0, 500, text="Item 1. Business. We make widgets.")
    warns = _check_brevity_sanity([short])
    assert warns and warns[0]["code"] == "suspect_brevity"


def test_brevity_item1_long_no_warn():
    long_item = _item("1", 0, 5000, text="Item 1. Business. " + "x" * 4000)
    assert _check_brevity_sanity([long_item]) == []


def test_brevity_skipped_for_other_items():
    short_1a = _item("1A", 0, 200, text="Item 1A. Risk Factors. Brief.")
    assert _check_brevity_sanity([short_1a]) == []


def test_title_match_canonical():
    item = _item(
        "1A", 0, 5000,
        text="Item 1A. Risk Factors\n\n" + "Risk content. " * 100,
    )
    assert _check_title_match([item]) == []


def test_title_match_via_alias():
    """AAPL FY1996 Item 4 says 'Submission of Matters to a Vote of Security
    Holders' — that's a known alias of post-2011 'Mine Safety Disclosures'.
    Alias path should make this pass without warning."""
    item = _item(
        "4", 0, 5000,
        text="Item 4. Submission of Matters to a Vote of Security Holders\n\n"
             + "Voting matters. " * 100,
    )
    assert _check_title_match([item]) == []


def test_title_mismatch_pre_2003_item_14():
    """Pre-2003 Item 14 was 'Exhibits...'; canonical title is now 'Principal
    Accountant Fees and Services'. Should warn."""
    item = _item(
        "14", 0, 5000,
        text="Item 14. Exhibits, Financial Statement Schedules, and Reports on Form 8-K\n\n"
             + "Exhibits content. " * 100,
    )
    warns = _check_title_match([item])
    assert warns and warns[0]["code"] == "title_mismatch"
    assert warns[0]["item"] == "14"


def test_title_skipped_for_non_extracted():
    """IBR / N/A / reserved sections have non-title content — skip the check."""
    ibr = _item(
        "10", 0, 500, status="incorporated_by_reference",
        text="Item 10. Directors\n\nThe information is incorporated by reference.",
    )
    reserved = _item("6", 0, 50, status="reserved", text="Item 6. [Reserved]")
    na = _item("4", 0, 100, status="not_applicable", text="Item 4. Mine Safety. Not applicable.")
    assert _check_title_match([ibr, reserved, na]) == []


@pytest.mark.asyncio
async def test_validate_skips_xbrl_when_requested():
    items = [_item("8", 0, 5000, text="Item 8. Financial Statements\n\n" + "Numbers. " * 500)]
    warns = await validate(items, doc_length=10000, meta=_meta(), skip_xbrl=True)
    codes = [w["code"] for w in warns]
    # No XBRL-related warnings should appear
    assert not any(c.startswith("xbrl_") for c in codes)


@pytest.mark.asyncio
async def test_validate_skips_xbrl_pre_2009():
    """XBRL mandate is post-2009; pre-2009 filings should not trigger XBRL check."""
    items = [_item("8", 0, 5000, text="Item 8. Financial Statements\n\n" + "Numbers. " * 500)]
    warns = await validate(
        items, doc_length=10000, meta=_meta(period="1996-09-27"), skip_xbrl=False,
    )
    codes = [w["code"] for w in warns]
    assert not any(c.startswith("xbrl_") for c in codes)
