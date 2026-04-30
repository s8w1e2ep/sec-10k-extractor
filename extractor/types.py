"""Shared dataclasses for the extraction pipeline."""

from dataclasses import dataclass, field
from typing import Literal


Status = Literal["extracted", "incorporated_by_reference", "not_applicable", "reserved"]
ResolvedBy = Literal["toc", "heading", "llm"]
FormatType = Literal["html_modern", "html_legacy", "plain_text"]


@dataclass(frozen=True)
class CanonicalItem:
    part: str
    item_number: str
    title: str
    aliases: tuple[str, ...] = ()
    valid_from_year: int | None = None
    valid_to_year: int | None = None
    optional: bool = False  # Item 16 is "may, at their option" per Form 10-K instructions


@dataclass
class Heading:
    level: int
    text: str
    char_offset: int


@dataclass
class Anchor:
    name: str
    char_offset: int


@dataclass
class NormalizedDoc:
    text: str
    headings: list[Heading]
    anchors: list[Anchor]
    format: FormatType


@dataclass
class ItemSpan:
    part: str
    item_number: str
    item_title: str
    start: int
    end: int
    resolved_by: ResolvedBy


@dataclass
class ExtractedItem:
    part: str
    item_number: str
    item_title: str
    content_text: str
    char_range_start: int
    char_range_end: int
    status: Status
    resolved_by: ResolvedBy


@dataclass
class FilingMetadata:
    cik: str
    accession_number: str
    form: str
    filing_date: str
    period_of_report: str | None
    primary_document_url: str
    company_name: str


@dataclass
class Warning_:
    code: str
    message: str
    extra: dict = field(default_factory=dict)
