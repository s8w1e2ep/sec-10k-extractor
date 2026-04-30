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


_SEC_DOC_TEXT_RE = re.compile(
    r"<DOCUMENT>\s*<TYPE>\s*10-K(?:/A)?\b.*?<TEXT>(.*?)</TEXT>\s*</DOCUMENT>",
    re.IGNORECASE | re.DOTALL,
)
_SEC_TAG_RE = re.compile(r"<[^>]+>")
_HEADING_LINE_RE = re.compile(
    r"^\s*(?:Item|ITEM)\s+\d{1,2}\s*[A-Z]?\b\.?",
    re.IGNORECASE,
)
_PART_LINE_RE = re.compile(r"^\s*PART\s+(?:I|II|III|IV)\b\.?\s*$", re.IGNORECASE)


def normalize_plain_text(raw: bytes) -> NormalizedDoc:
    """Normalize a pre-2002 plain-text EDGAR filing.

    Strips the SEC pseudo-XML wrapper if present, replaces form-feeds with
    newlines, and detects 'Item N.' / 'PART X' lines as headings.
    """
    text = raw.decode("utf-8", errors="replace")

    # Extract the 10-K content from the SEC pseudo-XML wrapper, if present
    m = _SEC_DOC_TEXT_RE.search(text)
    if m:
        text = m.group(1)
    else:
        # Strip lone <SEC-HEADER>...</SEC-HEADER> if no DOCUMENT block matched
        text = re.sub(
            r"<SEC-HEADER>.*?</SEC-HEADER>", "", text, flags=re.IGNORECASE | re.DOTALL
        )

    # Strip remaining pseudo-XML tags (<TYPE>, <SEQUENCE>, etc.)
    text = _SEC_TAG_RE.sub("", text)

    # Normalize line endings and form-feeds
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")

    # Strip trailing whitespace per line; collapse runs of blank lines to max 2
    lines = [line.rstrip() for line in text.split("\n")]
    cleaned: list[str] = []
    blank_run = 0
    for line in lines:
        if not line.strip():
            blank_run += 1
            if blank_run <= 2:
                cleaned.append("")
        else:
            blank_run = 0
            cleaned.append(line)

    # Trim leading/trailing blank lines
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()

    out_text = "\n".join(cleaned) + ("\n" if cleaned else "")

    # Detect headings: walk lines, recompute offsets in out_text.
    # Require previous line to be blank — strong signal of section start.
    # We don't require next-blank because heading titles often wrap to the
    # next line (e.g. "Item 5. Market for the Registrant's Common Equity and
    # Related Stockholder Matters" continues on line 2). Headings are
    # additionally constrained to short lines (< 200 chars).
    headings: list[Heading] = []
    pos = 0
    for i, line in enumerate(cleaned):
        line_start = pos
        stripped = line.strip()
        if stripped:
            prev_blank = (i == 0) or (not cleaned[i - 1].strip())
            if prev_blank:
                if _HEADING_LINE_RE.match(stripped) and len(stripped) < 200:
                    headings.append(
                        Heading(level=2, text=stripped, char_offset=line_start)
                    )
                elif _PART_LINE_RE.match(stripped) and len(stripped) < 80:
                    headings.append(
                        Heading(level=1, text=stripped, char_offset=line_start)
                    )
        pos += len(line) + 1

    return NormalizedDoc(
        text=out_text, headings=headings, anchors=[], format="plain_text"
    )
