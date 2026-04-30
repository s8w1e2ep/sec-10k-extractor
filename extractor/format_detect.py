"""Detect filing format: html_modern | html_legacy | plain_text."""

from .types import FormatType


def detect_format(raw: bytes, *, content_type: str = "", url: str = "") -> FormatType:
    sample = raw[:8192].decode("utf-8", errors="replace").lower()

    is_html = (
        "text/html" in content_type.lower()
        or "<html" in sample
        or "<!doctype html" in sample
    )
    if not is_html and ("<" in sample and ">" in sample) and "<sec-document>" not in sample:
        # Fallback HTML detection for stripped-doctype filings
        if any(t in sample for t in ("<body", "<div", "<table", "<p ", "<p>")):
            is_html = True

    if not is_html:
        if url.lower().endswith(".txt") or "text/plain" in content_type.lower():
            return "plain_text"
        return "plain_text"

    if "xmlns:ix" in sample or "<ix:" in sample:
        return "html_modern"
    return "html_legacy"
