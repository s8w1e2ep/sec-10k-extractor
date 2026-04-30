# 10-K Filing Format Eras

Reference for picking eval fixtures. When the SEC changes Form 10-K rules, the structure or content of filings shifts — fixtures spanning these eras catch parser regressions early.

Dates below are by **fiscal year (FY)**: FY 2024 means the 10-K covering fiscal year ending in 2024 (typically filed early 2025). The cutoffs are slightly fuzzy because SEC rules apply to "fiscal years ending on or after [date]" and smaller reporting companies often get phase-in delays.

## Timeline (most → least recent)

| Era | What changed | Source |
|---|---|---|
| **FY 2023+** | **Item 1C Cybersecurity added.** Mandatory disclosure of cybersecurity risk management, strategy, governance, and material incidents. Effective for fiscal years ending on or after 2023-12-15 (large filers); 180-day delay for smaller reporting companies. | SEC Final Rule, July 2023 |
| **FY 2021+** | **Item 9C added.** "Disclosure Regarding Foreign Jurisdictions That Prevent Inspections" (Holding Foreign Companies Accountable Act / HFCAA). Mainly populated by Commission-Identified Issuers (auditors in jurisdictions blocking PCAOB inspection — primarily China-based). Most US filers leave it short or "Not Applicable". | SEC Final Rule, December 2021 (implementing HFCAA, December 2020) |
| **FY 2020+** | **Item 6 → [Reserved].** "Selected Financial Data" requirement eliminated. Item 6 becomes literally "[Reserved]" with no content. Some smaller reporting companies on transition relief through FY 2021. | SEC Final Rule, August 2020 (effective February 2021) |
| **FY 2019+** | **Inline XBRL (iXBRL) phased in.** XBRL tags embedded directly in 10-K HTML via `<ix:...>` namespace, instead of separate XML exhibits. Phase-in: large accelerated filers FY 2019; accelerated FY 2020; smaller reporting FY 2021. | SEC Final Rule, June 2018 |
| **FY 2011+** | **Item 4 became "Mine Safety Disclosures"** (Dodd-Frank). Prior Item 4 was "Submission of Matters to a Vote of Security Holders". Most non-mining filers now write "Not Applicable". | Dodd-Frank Act, July 2010 |
| **FY 2009+** | **XBRL data (separate exhibits)** mandated for financial statements. Lives in `.xml` sibling files, not in the 10-K HTML itself. SEC's Company Facts API draws from this era forward — older filings have no XBRL cross-validation signal. | SEC Final Rule, January 2009 |
| **2002–2008** | **Legacy HTML era.** HTML primary documents allowed and gradually adopted, but structure inconsistent: TOCs often without `id` anchors, headings written as bold paragraphs rather than `<h1>..<h6>`. | -- |
| **1993–2001** | **Plain-text era.** EDGAR launched 1993; only `.txt` files. Form-feed (`\f`) for page breaks; ALL-CAPS lines for headings; wildly varied. | -- |

## How to read this when picking fixtures

| Eval category | Pick from era | Notes |
|---|---|---|
| `modern_clean` | FY 2020+ | iXBRL, clean TOC anchors. AAPL FY 2024, MSFT FY 2024 are the easy baseline. |
| `new_items_2023` | FY 2023+ | Must include Item 1C; verify it's marked `extracted` (not all smaller filers have it yet — 180-day delay). |
| `incorporation_heavy` | FY 2010+ (any) | Items 10–14 incorporated by reference is era-independent; just pick a large-cap filer. |
| `plain_text` | FY 1996–2000 | Avoid 2001–2002 boundary years (mixed HTML/text edge cases). |
| `amendment` | FY 2020+ | Keep modern to avoid double-stressing parser (plain-text + amendment together is too much for v1). |
| `mining` | FY 2011+ | Pre-2011 Item 4 was different — would confuse the validator. Real mining: companies like NEM, FCX, BTU. |
| `bank` | FY 2020+ | Industry Guide 3 disclosures complicate Item 8; pick recent. |
| `small_cap` | FY 2021+ | Smaller reporting companies are on later iXBRL phase-in; FY 2021+ ensures they're on iXBRL. |

## Things to verify on each candidate filing before committing as a fixture

For each candidate, open the original 10-K's TOC and confirm:

1. **Items present** — some filings legitimately omit items (EGCs may omit some compensation discussion).
2. **Which items are incorporated by reference** — varies by filer, especially Items 10-14.
3. **Item 4 status** — "Not Applicable" (non-mining) or actual content.
4. **Item 1C presence** — only FY 2023+ for large filers.
5. **Item 9C content** — "Not Applicable" (most US filers), short reference (some), or substantive (China-exposed filers).
6. **Item 6 wording** — "[Reserved]" (FY 2020+) or actual Selected Financial Data (FY 2019 and earlier).

These become the `expected_items_present` and `expected_status_overrides` fields in `eval/fixtures/filings.jsonl`.

## Sources

- SEC Form 10-K General Instructions: https://www.sec.gov/files/form10-k.pdf
- 17 CFR §229 (Regulation S-K)
- Final rules referenced above are searchable at https://www.sec.gov/rules/final
