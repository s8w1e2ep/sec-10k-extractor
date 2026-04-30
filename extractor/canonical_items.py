"""The 23-item 10-K catalog as of FY 2023+."""

from datetime import date

from rapidfuzz import fuzz

from .types import CanonicalItem


CANONICAL_ITEMS: list[CanonicalItem] = [
    # Part I
    CanonicalItem("I", "1", "Business"),
    CanonicalItem("I", "1A", "Risk Factors"),
    CanonicalItem("I", "1B", "Unresolved Staff Comments"),
    CanonicalItem("I", "1C", "Cybersecurity", valid_from_year=2023),
    CanonicalItem("I", "2", "Properties"),
    CanonicalItem("I", "3", "Legal Proceedings"),
    CanonicalItem(
        "I",
        "4",
        "Mine Safety Disclosures",
        aliases=("Submission of Matters to a Vote of Security Holders",),
    ),
    # Part II
    CanonicalItem(
        "II",
        "5",
        "Market for Registrant's Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities",
        aliases=("Market for Registrant's Common Equity",),
    ),
    CanonicalItem(
        "II",
        "6",
        "[Reserved]",
        aliases=("Reserved", "Selected Financial Data"),
    ),
    CanonicalItem(
        "II",
        "7",
        "Management's Discussion and Analysis of Financial Condition and Results of Operations",
        aliases=("Management's Discussion and Analysis", "MD&A"),
    ),
    CanonicalItem(
        "II",
        "7A",
        "Quantitative and Qualitative Disclosures About Market Risk",
    ),
    CanonicalItem("II", "8", "Financial Statements and Supplementary Data"),
    CanonicalItem(
        "II",
        "9",
        "Changes in and Disagreements with Accountants on Accounting and Financial Disclosure",
    ),
    CanonicalItem("II", "9A", "Controls and Procedures"),
    CanonicalItem("II", "9B", "Other Information"),
    CanonicalItem(
        "II",
        "9C",
        "Disclosure Regarding Foreign Jurisdictions That Prevent Inspections",
        valid_from_year=2021,
    ),
    # Part III
    CanonicalItem(
        "III", "10", "Directors, Executive Officers and Corporate Governance"
    ),
    CanonicalItem("III", "11", "Executive Compensation"),
    CanonicalItem(
        "III",
        "12",
        "Security Ownership of Certain Beneficial Owners and Management and Related Stockholder Matters",
    ),
    CanonicalItem(
        "III",
        "13",
        "Certain Relationships and Related Transactions, and Director Independence",
    ),
    CanonicalItem("III", "14", "Principal Accountant Fees and Services"),
    # Part IV
    CanonicalItem("IV", "15", "Exhibits, Financial Statement Schedules"),
    CanonicalItem("IV", "16", "Form 10-K Summary"),
]


_PART_ORDER = {"I": 0, "II": 1, "III": 2, "IV": 3}


def sort_key(item_number: str) -> tuple[int, str]:
    """Stable ordering across 1, 1A, 1B, 1C, 2, ..."""
    s = item_number.strip().upper()
    i = 0
    while i < len(s) and s[i].isdigit():
        i += 1
    if i == 0:
        return (999, s)
    return (int(s[:i]), s[i:])


def part_sort_key(part: str, item_number: str) -> tuple[int, int, str]:
    n_int, n_suffix = sort_key(item_number)
    return (_PART_ORDER.get(part, 99), n_int, n_suffix)


def expected_items_for_period(period_of_report: date | None) -> list[CanonicalItem]:
    """Filter the catalog to items applicable to a given filing period."""
    if period_of_report is None:
        return list(CANONICAL_ITEMS)
    year = period_of_report.year
    out = []
    for item in CANONICAL_ITEMS:
        if item.valid_from_year is not None and year < item.valid_from_year:
            continue
        if item.valid_to_year is not None and year > item.valid_to_year:
            continue
        out.append(item)
    return out


def fuzzy_match_title(found_title: str, threshold: int = 80) -> CanonicalItem | None:
    """Return the canonical item whose title best matches the given heading text."""
    found = found_title.strip().lower()
    if not found:
        return None
    best_score = 0
    best_item: CanonicalItem | None = None
    for item in CANONICAL_ITEMS:
        candidates = [item.title.lower()] + [a.lower() for a in item.aliases]
        for candidate in candidates:
            score = fuzz.partial_ratio(found, candidate)
            if score > best_score:
                best_score = score
                best_item = item
    if best_score >= threshold:
        return best_item
    return None


def get_canonical_item(item_number: str) -> CanonicalItem | None:
    target = item_number.strip().upper()
    for item in CANONICAL_ITEMS:
        if item.item_number.upper() == target:
            return item
    return None
