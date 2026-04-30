from extractor.normalizer import normalize_plain_text


def test_basic_passthrough():
    raw = b"Hello world.\nLine two."
    doc = normalize_plain_text(raw)
    assert doc.format == "plain_text"
    assert "Hello world." in doc.text
    assert "Line two." in doc.text
    assert doc.anchors == []


def test_form_feed_replaced_with_newline():
    raw = b"Page one content.\fPage two content."
    doc = normalize_plain_text(raw)
    assert "Page one content." in doc.text
    assert "Page two content." in doc.text
    assert "\f" not in doc.text


def test_crlf_normalized():
    raw = b"Line1\r\nLine2\rLine3\n"
    doc = normalize_plain_text(raw)
    assert "\r" not in doc.text
    assert doc.text.count("\n") >= 2


def test_collapse_blank_runs_to_max_two():
    """Max 2 blank lines between content (= max 3 consecutive newlines)."""
    raw = b"A\n\n\n\n\nB\n"
    doc = normalize_plain_text(raw)
    assert "\n\n\n\n" not in doc.text


def test_detects_item_headings():
    raw = b"""

ITEM 1.  BUSINESS

Acme Corp. designs widgets.

ITEM 1A.  RISK FACTORS

Various risks apply.

ITEM 2.  PROPERTIES

Our office is in Cupertino.
"""
    doc = normalize_plain_text(raw)
    heading_texts = [h.text for h in doc.headings]
    assert any("ITEM 1" in t and "BUSINESS" in t.upper() for t in heading_texts)
    assert any("ITEM 1A" in t for t in heading_texts)
    assert any("ITEM 2" in t for t in heading_texts)


def test_strips_sec_document_wrapper():
    raw = b"""<SEC-DOCUMENT>0000320193-97-000005.txt
<SEC-HEADER>
ACCESSION NUMBER:		0000320193-97-000005
</SEC-HEADER>
<DOCUMENT>
<TYPE>10-K
<SEQUENCE>1
<TEXT>

ITEM 1. BUSINESS

Apple Computer designs personal computers.

</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>
"""
    doc = normalize_plain_text(raw)
    assert "Apple Computer designs personal computers." in doc.text
    assert "<SEC-DOCUMENT>" not in doc.text
    assert "<TEXT>" not in doc.text
    heading_texts = [h.text for h in doc.headings]
    assert any("ITEM 1" in t for t in heading_texts)


def test_heading_offset_points_into_text():
    raw = b"""

PART I

ITEM 1. BUSINESS

We make widgets.
"""
    doc = normalize_plain_text(raw)
    for h in doc.headings:
        # The heading text should appear in doc.text starting at char_offset
        assert doc.text[h.char_offset:h.char_offset + len(h.text)] == h.text
