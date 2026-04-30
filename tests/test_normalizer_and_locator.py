from bs4 import BeautifulSoup

from extractor.locator import locate_by_toc_anchor
from extractor.normalizer import normalize_html


_SMALL_FILING = """<!DOCTYPE html>
<html><body>
  <table>
    <tr><td><a href="#item1">Item 1.</a></td><td>Business</td></tr>
    <tr><td><a href="#item1a">Item 1A.</a></td><td>Risk Factors</td></tr>
    <tr><td><a href="#item6">Item 6.</a></td><td>[Reserved]</td></tr>
  </table>

  <div id="item1"><h2>Item 1. Business</h2>
    <p>Acme Corp. designs widgets and sells them.</p>
  </div>

  <div id="item1a"><h2>Item 1A. Risk Factors</h2>
    <p>Various risks apply to our business.</p>
  </div>

  <div id="item6"><h2>Item 6. [Reserved]</h2>
    <p>[Reserved]</p>
  </div>
</body></html>"""


def test_normalizer_captures_anchors_and_headings():
    soup = BeautifulSoup(_SMALL_FILING, "lxml")
    doc = normalize_html(soup, "html_modern")

    names = [a.name for a in doc.anchors]
    assert "item1" in names
    assert "item1a" in names
    assert "item6" in names

    heading_texts = [h.text for h in doc.headings]
    assert any("Business" in t for t in heading_texts)
    assert any("Risk Factors" in t for t in heading_texts)
    assert any("[Reserved]" in t for t in heading_texts)


def test_anchor_offsets_point_into_normalized_text():
    soup = BeautifulSoup(_SMALL_FILING, "lxml")
    doc = normalize_html(soup, "html_modern")
    anchor_offset = {a.name: a.char_offset for a in doc.anchors}

    item1_offset = anchor_offset["item1"]
    item1a_offset = anchor_offset["item1a"]
    item6_offset = anchor_offset["item6"]

    # Anchors are in document order
    assert item1_offset < item1a_offset < item6_offset

    # Text starting at item1 contains the section heading
    assert "Item 1. Business" in doc.text[item1_offset:item1a_offset]
    # Text starting at item1a contains Risk Factors
    assert "Risk Factors" in doc.text[item1a_offset:item6_offset]
    # Text starting at item6 contains Reserved
    assert "[Reserved]" in doc.text[item6_offset:]


def test_locator_finds_three_items():
    soup = BeautifulSoup(_SMALL_FILING, "lxml")
    doc = normalize_html(soup, "html_modern")
    spans = locate_by_toc_anchor(soup, doc)

    nums = [s.item_number for s in spans]
    assert nums == ["1", "1A", "6"]
    assert all(s.resolved_by == "toc" for s in spans)
    # All spans non-empty and ordered
    for s in spans:
        assert s.start < s.end
    for a, b in zip(spans, spans[1:]):
        assert a.end <= b.start or b.start <= a.start  # ordering by part-sort, not offset
