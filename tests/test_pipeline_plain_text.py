"""Plain-text pipeline test against a real pre-2002 EDGAR filing.

Uses Apple Inc. FY 1996 10-K (accession 0000320193-96-000023) as the fixture —
271 KB, plain-text wrapped in SEC pseudo-XML. This exercises the entire
plain-text path (wrapper-strip, form-feed handling, isolated-line heading
detection, heading_regex locator) without requiring network.
"""

from pathlib import Path

from extractor.locator import combine_strategies, locate_by_heading_regex
from extractor.normalizer import normalize_plain_text
from extractor.status_detect import detect_status

FIXTURE = Path(__file__).parent / "fixtures" / "aapl_1996_10k.txt"


def _load_aapl_1996() -> bytes:
    return FIXTURE.read_bytes()


def test_plain_text_normalizer_strips_sec_wrapper():
    raw = _load_aapl_1996()
    doc = normalize_plain_text(raw)
    assert doc.format == "plain_text"
    assert "<SEC-DOCUMENT>" not in doc.text
    assert "<TEXT>" not in doc.text
    assert "<TYPE>10-K" not in doc.text
    # Real content should be present
    assert "Apple Computer" in doc.text or "Apple" in doc.text


def test_plain_text_finds_expected_pre_2003_items():
    raw = _load_aapl_1996()
    doc = normalize_plain_text(raw)
    spans = locate_by_heading_regex(doc)
    found_numbers = {s.item_number for s in spans}

    # Pre-2003 Form 10-K had Items 1-14 (no 1A/1B/1C/7A/9A/9B/15/16).
    # AAPL 1996 should have all 14.
    expected = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14"}
    missing = expected - found_numbers
    assert not missing, f"Missing items: {missing}"


def test_plain_text_locator_attribution():
    raw = _load_aapl_1996()
    doc = normalize_plain_text(raw)
    spans = locate_by_heading_regex(doc)
    assert all(s.resolved_by == "heading" for s in spans)


def test_plain_text_combine_with_empty_toc():
    raw = _load_aapl_1996()
    doc = normalize_plain_text(raw)
    heading_spans = locate_by_heading_regex(doc)
    # Plain text has no TOC anchors → toc_spans is empty.
    spans, warnings = combine_strategies([], heading_spans, doc_length=len(doc.text))
    assert len(spans) == len(heading_spans)
    assert warnings == []


def test_plain_text_status_detection_on_real_content():
    raw = _load_aapl_1996()
    doc = normalize_plain_text(raw)
    spans = locate_by_heading_regex(doc)
    by_num = {s.item_number: s for s in spans}

    # Item 1 (Business) should be substantial → extracted
    item1 = by_num["1"]
    item1_text = doc.text[item1.start:item1.end]
    assert detect_status("1", item1_text) == "extracted"
    assert len(item1_text) > 5000  # AAPL 1996 Item 1 is ~24 KB

    # Item 9 (Changes in and Disagreements with Accountants) is typically empty
    # for AAPL — should be detected as not_applicable.
    item9 = by_num["9"]
    item9_text = doc.text[item9.start:item9.end]
    assert detect_status("9", item9_text) == "not_applicable"
