"""Normalize HTML 10-Ks to plain text with offset-tracked headings + anchors.

The normalized text is the single source of truth for `char_range` offsets.
Any change here invalidates char-range golden tests on purpose — this is the
contract with the rest of the pipeline.
"""

import re

from bs4 import BeautifulSoup, NavigableString, Tag

from .types import Anchor, FormatType, Heading, NormalizedDoc


_BLOCK_TAGS = {
    "p", "div", "section", "article", "table", "tr", "li",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
    "ol", "ul", "tbody", "thead", "tfoot",
}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_DROP_TAGS = {"script", "style", "noscript", "head", "meta", "link"}


def normalize_html(soup: BeautifulSoup, fmt: FormatType = "html_modern") -> NormalizedDoc:
    out: list[str] = []
    pos = 0
    last_char = ""
    headings: list[Heading] = []
    anchors: list[Anchor] = []
    seen_anchors: set[str] = set()

    def emit(s: str) -> None:
        nonlocal pos, last_char
        if not s:
            return
        if s == " " and last_char in (" ", "\n", ""):
            return
        out.append(s)
        pos += len(s)
        last_char = s[-1]

    def break_line() -> None:
        nonlocal pos, last_char
        if last_char in ("\n", ""):
            return
        out.append("\n")
        pos += 1
        last_char = "\n"

    def get_text(node) -> str:
        if isinstance(node, NavigableString):
            return str(node)
        if not isinstance(node, Tag):
            return ""
        return "".join(get_text(c) for c in node.children)

    def walk(node) -> None:
        nonlocal pos, last_char

        if isinstance(node, NavigableString):
            text = str(node)
            collapsed = re.sub(r"\s+", " ", text)
            if not collapsed.strip():
                if last_char not in (" ", "\n", ""):
                    emit(" ")
                return
            if text and text[0].isspace() and last_char not in (" ", "\n", ""):
                emit(" ")
            emit(collapsed.strip())
            if text and text[-1].isspace():
                emit(" ")
            return

        if not isinstance(node, Tag):
            return

        if node.name in _DROP_TAGS:
            return

        anchor_id = node.get("id") or node.get("name")
        if isinstance(anchor_id, str) and anchor_id and anchor_id not in seen_anchors:
            anchors.append(Anchor(name=anchor_id, char_offset=pos))
            seen_anchors.add(anchor_id)

        if node.name in _HEADING_TAGS:
            break_line()
            heading_start = pos
            raw = get_text(node)
            collapsed = re.sub(r"\s+", " ", raw).strip()
            if collapsed:
                emit(collapsed)
                level = int(node.name[1])
                headings.append(
                    Heading(level=level, text=collapsed, char_offset=heading_start)
                )
            break_line()
            return

        if node.name in _BLOCK_TAGS:
            break_line()
            for child in node.children:
                walk(child)
            break_line()
            return

        if node.name in ("br", "hr"):
            break_line()
            return

        for child in node.children:
            walk(child)

    body = soup.body or soup
    walk(body)

    text = "".join(out)
    return NormalizedDoc(text=text, headings=headings, anchors=anchors, format=fmt)
