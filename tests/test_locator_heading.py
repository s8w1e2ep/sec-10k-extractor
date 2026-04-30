from extractor.locator import combine_strategies, locate_by_heading_regex
from extractor.types import Heading, ItemSpan, NormalizedDoc


def _doc_with_headings(text: str, headings: list[tuple[str, int]]) -> NormalizedDoc:
    return NormalizedDoc(
        text=text,
        headings=[Heading(level=2, text=h, char_offset=o) for h, o in headings],
        anchors=[],
        format="html_legacy",
    )


def test_heading_regex_finds_simple_sequence():
    doc = _doc_with_headings(
        "x" * 5000,
        [
            ("Item 1. Business", 100),
            ("Item 1A. Risk Factors", 1000),
            ("Item 2. Properties", 2000),
        ],
    )
    spans = locate_by_heading_regex(doc)
    nums = [s.item_number for s in spans]
    assert nums == ["1", "1A", "2"]
    assert all(s.resolved_by == "heading" for s in spans)
    assert spans[0].start == 100
    assert spans[0].end == 1000


def test_heading_regex_drops_out_of_canonical_order():
    """A 'Item 1' heading appearing AFTER 'Item 2' is dropped (TOC echo)."""
    doc = _doc_with_headings(
        "x" * 5000,
        [
            ("Item 1. Business", 100),
            ("Item 2. Properties", 1000),
            ("Item 1. Business (TOC echo)", 1500),  # out of order
            ("Item 3. Legal", 2000),
        ],
    )
    spans = locate_by_heading_regex(doc)
    nums = [s.item_number for s in spans]
    assert nums == ["1", "2", "3"]


def test_heading_regex_drops_unknown_items():
    doc = _doc_with_headings(
        "x" * 5000,
        [
            ("Item 1. Business", 100),
            ("Item 99. Made Up", 500),
            ("Item 2. Properties", 1000),
        ],
    )
    spans = locate_by_heading_regex(doc)
    nums = [s.item_number for s in spans]
    assert nums == ["1", "2"]


def test_combine_toc_wins_on_disagreement():
    toc = [ItemSpan("I", "1", "Business", 1000, 5000, "toc")]
    heading = [ItemSpan("I", "1", "Business", 5000, 6000, "heading")]
    spans, warnings = combine_strategies(toc, heading, doc_length=10000)
    assert len(spans) == 1
    assert spans[0].start == 1000
    assert spans[0].resolved_by == "toc"
    assert any(w["code"] == "strategy_disagreement" for w in warnings)


def test_combine_no_warning_for_close_starts():
    toc = [ItemSpan("I", "1", "Business", 1000, 5000, "toc")]
    heading = [ItemSpan("I", "1", "Business", 1050, 6000, "heading")]
    spans, warnings = combine_strategies(toc, heading, doc_length=10000)
    assert len(spans) == 1
    assert spans[0].start == 1000
    assert warnings == []


def test_combine_fills_gaps_from_heading():
    toc = [ItemSpan("I", "1", "Business", 1000, 2000, "toc")]
    heading = [
        ItemSpan("I", "1A", "Risk Factors", 2000, 3000, "heading"),
        ItemSpan("I", "2", "Properties", 3000, 4000, "heading"),
    ]
    spans, warnings = combine_strategies(toc, heading, doc_length=10000)
    nums = [(s.item_number, s.resolved_by) for s in spans]
    assert nums == [("1", "toc"), ("1A", "heading"), ("2", "heading")]
    assert warnings == []
    # End-recompute: Item 1's end should equal Item 1A's start
    assert spans[0].end == 2000
    assert spans[1].end == 3000
    assert spans[2].end == 10000  # last item gets doc_length


def test_combine_empty_inputs():
    spans, warnings = combine_strategies([], [], doc_length=10000)
    assert spans == []
    assert warnings == []
