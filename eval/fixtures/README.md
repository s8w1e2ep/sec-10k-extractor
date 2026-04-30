# eval/fixtures/README.md — Why each fixture is in the eval set

The eval set is **hand-curated**, not auto-sampled. Each fixture is here
to stress a specific era, industry, or filer-type pattern that random
sampling would under-represent. Spec §5.1 mandates 8 categories with
≥1 fixture each — we cover 6 of them with 16 hand-picked filings (the
two uncovered, `amendment` and `small_cap`, are documented at the bottom
of this file).

The discipline behind `expected_status_overrides`: only label what is
verifiable by reading the actual filing content (via
[`eval/inspect_filing.py`](../inspect_filing.py)). **Do not** reflect
back what the pipeline said — that turns the metric into a self-fulfilling
copy. When pipeline disagrees with override and the *override* is wrong,
the override gets dropped (see Apollo / Disney FY 2002 below).

---

## modern_clean (4)

The "happy path" — recent inline-XBRL filings from large public companies.
These are what every grader will hit first. Fixtures here exist to make
sure the TOC-anchor strategy works on the dominant filing format. If
these fixtures fail we have a fundamental locator regression.

### 1. Apple Inc. — FY 2025 (CIK 320193)

The single cleanest baseline. `Item N. Title` link text on every TOC
entry, distinct anchors per item, full 23-item set including 1C
Cybersecurity (post-2023) and 9C Foreign Jurisdictions (post-2021).
Smoke-tested in Phase 1; this is the fixture that proves the loop works
before we scale to messier cases.

`expected_status_overrides`: 10 entries — the most thoroughly hand-labelled
fixture, smoke-confirmed in Phase 1.

### 2. Microsoft Corp. — FY 2025 (CIK 789019)

Surfaced **TOC pattern bug #1**: MSFT puts the item *number* in the
href fragment (`#item_1_business`) and only the *title* in the link
text. AAPL-style "Item N. Title" link-text matching missed every MSFT
item. Drove the addition of the href-fallback path in
`extractor/locator.py:_extract_toc_entries`.

### 3. NVIDIA Corp. — FY 2026 (CIK 1045810)

Different inline-XBRL template than AAPL/MSFT — confirms the TOC
strategy generalizes across renderers. Item 9C in NVDA's text bleeds
into a "Part III ... will file proxy" preamble (~427 chars total, a
trailing IBR phrase appears in the bleed) — exposed the
`_NOT_APPLICABLE_RE` length cap that needed to grow from 300 → 500
in Phase D.

### 4. Tesla, Inc. — FY 2025 (CIK 1318605)

Surfaced **TOC pattern bug #3**: Tesla splits "Item 1C." across two
adjacent anchors (number in one, sub-letter in another). Single-`<a>`
matching gave us "1" instead of "1C", losing the new-2023 cybersecurity
item. Drove the row-level (`<tr>`/`<li>`) extraction strategy.

---

## incorporation_heavy (3)

Items 10–14 are SEC-eligible to be incorporated by reference from a
DEF 14A proxy statement (Form 10-K General Instruction G(3)).
"Incorporation-heavy" filers exercise that provision; status detection
has to recognize the textual marker. Three fixtures with **three
distinct IBR phrasings** so we don't overfit to one filer's wording.

### 5. Berkshire Hathaway Inc. — FY 2025 (CIK 1067983)

Berkshire writes Items 10-14 with one shared boilerplate paragraph
("Except for the information set forth under the caption "Executive
Officers of the Registrant" in Part I hereof, information required by
this Part (Items 10, 11, 12, 13 and 14) is incorporated by reference
from..."). The unusual opener — without the canonical "incorporated
herein by reference" — surfaced the **`_INCORPORATED_RE` regex bug**
that silently required at least one "i" before "by reference"
(Phase D). Phase 4 LLM status resolver also targets BRK Item 14: when
title_mismatch fires, the LLM confirms the IBR status.

### 6. Walmart Inc. — FY 2026 (CIK 104169)

Multi-line item heading (`ITEM 1C.\nCYBERSECURITY` across two lines)
broke the single-line heading regex during Phase 8. Drove the
`_extract_section_heading` Walmart-style multi-line fallback in
`extractor/validator.py`.

### 7. Apollo Asset Management — FY 2022 (CIK 1411494)

Originally targeted as "small_cap" (mistaking CIK 1411494 for Stitch
Fix); the Submissions API came back with Apollo Asset Management — a
$40 B+ asset manager — which is the canonical mishap documented in
`prompts/03-eval-set-design.md`. Recategorized as `incorporation_heavy`
because asset managers were assumed to use IBR.

**Phase D correction**: actually Apollo *does not* incorporate Items
10-14 by reference — they wrote 1.9–54.7 KB of substantive prose
directly per item. My initial overrides (10-14 = IBR) were dropped after
reading the filing content. The fixture remains valuable: the
`pre_2023` era (no Item 1C), and Apollo's TOC anchor structure (GUID
hashes split across cells) was bug #2 from Phase 5 — drove the
row-level extraction generalization.

---

## bank (1)

### 8. JPMorgan Chase & Co. — FY 2025 (CIK 19617)

Banks have huge Item 8 sections (Industry Guide 3 disclosures push
financial statements past 100 KB) — confirms the normalizer scales.
JPM also exposed the **compound-IBR pattern**: Item 10 carries the
master IBR statement, and Items 11-14 are short cross-references
("Refer to Item 10."). Pure rules without a cross-reference detector
mis-classified them as extracted, driving the `_CROSS_REF_RE` rule
in Phase D.

---

## mining (1)

### 9. Newmont Corp. — FY 2025 (CIK 1164727)

Item 4 (Mine Safety Disclosures, post-Dodd-Frank 2011) is genuinely
populated rather than the boilerplate "Not applicable." every other
fixture has — confirms our pipeline doesn't classify a substantively-
populated Item 4 as N/A by accident.

---

## small_cap + restaurant (1)

### 10. Kura Sushi USA, Inc. — FY 2025 (CIK 1772177)

The actual small-cap fixture (~$300M market cap; replaced the
mis-labeled Apollo). Surfaced the **shared-TOC-anchor pattern**: Kura
Sushi puts Items 11-14 under one anchor `part_iii_items_11_through_14`
while Item 10 has its own anchor 8 chars later in the doc. Canonical-
order end computation produced an inverted span (`char_range_invalid`).
Drove two fixes in Phase C: `combine_strategies` end-computation
switched from canonical-adjacency to offset-adjacency, and
`_check_char_range_overlap` now walks by offset order with identical-
range suppression. Items 11-14 still get only an 8-char shared fragment
each — the third Items-10-14 status heuristic ("very short content
in an IBR-eligible item ⇒ IBR") covers this in Phase D.

---

## restaurant (1)

### 11. Chipotle Mexican Grill Inc. — FY 2025 (CIK 1058090)

Mid-cap restaurant — pairs with Kura Sushi to test the "restaurant"
industry across two market caps and TOC structures. Kura Sushi is
small + shared-anchor; Chipotle is mid + standard. Together they
prevent overfitting to one filer's pattern.

---

## biotech (1)

### 12. Moderna, Inc. — FY 2025 (CIK 1682852)

R&D-heavy filer with the largest Item 1A (Risk Factors) in the eval
set (~190 KB) — confirms normalizer/locator scale on long sections.
Moderna's Item 9C says "None Applicable." (typo for "Not Applicable")
which the original `_NOT_APPLICABLE_RE` didn't match. Drove the
"none applicable" alternation in Phase D.

---

## industrial (1)

### 13. Caterpillar Inc. — FY 2025 (CIK 18230)

Heavy-machinery / traditional-industry filer — counterbalances the
heavy tech / finance bias of the other modern fixtures. Same template
as JPM (executive officer table inline, IBR statement after) —
confirms the Phase D Items-10-14 full-text IBR search generalizes.

---

## luxury_retail (1)

### 14. Tiffany & Co. — FY 2014/2015 (CIK 98246)

Pre-2020 era fixture: Item 6 was "Selected Financial Data" (extracted)
rather than `[Reserved]`; pre-2021 means no Item 9C; post-2005 means
Items 1A and 1B both present. LVMH itself files 20-F (foreign issuer)
and is out of scope; Tiffany was the closest US-listed luxury
equivalent that filed 10-K before LVMH acquired it in 2020. Tests the
era-gating logic in `canonical_items.py:expected_items_for_period`
across an FY between two of the cutoffs.

---

## entertainment + pre_sox (1)

### 15. Walt Disney Co. — FY 2002 (CIK 1001039)

Pre-Sarbanes-Oxley filing. Item 1A / 1B / 9A / 9B / 15 don't exist
yet (era-gated to 2003+ in the catalog). Item 4 was "Submission of
Matters to a Vote of Security Holders" (renamed to Mine Safety in
2011); Item 14 was "Controls and Procedures" (the SOX-driven content
that later moved to Item 9A and got renumbered). Confirms the catalog's
era cutoffs (`valid_from_year`) work correctly on a transition-era
filing. Items 10-12 use IBR; Items 13-14 are extracted (Disney wrote
substantive content for those). The validator's `title_mismatch`
warning correctly flags Item 14 as era-mismatched (the LLM Layer 1
confirms the status on live).

---

## plain_text (1)

### 16. Apple Computer Inc. — FY 1996 (CIK 320193, file_url path)

Pre-2002 SEC pseudo-XML wrapper around plain text. Confirms
`extractor/normalizer.py:normalize_plain_text` handles the form-feed
line breaks and isolated-line heading detection. Resolved entirely
via the heading-regex strategy (no TOC anchors in plain text).
Title_mismatch warning on Item 14 (era-rename: pre-SOX "Exhibits..."
under post-SOX canonical title) is a known limitation — the content
is correctly located but the response title is era-mismatched. Phase 8
confirmed the validator catches this; Phase 4 LLM resolver keeps the
status correctly `extracted` (the content really is exhibits prose).

---

## What we're NOT testing

These categories from `spec.md` §5.1 are uncovered. Both are noted in
`task.md` Phase 8 backlog and `decisions.md`.

### `amendment` (10-K/A)

Per Phase A's form-gating: 10-K/A amendments are explicitly out of
scope. The service returns HTTP 400 with a structured "Unsupported form"
detail. No fixture needed.

### `small_cap`

Kura Sushi USA covers small-cap restaurant; we don't have a small-cap
*outside* the restaurant industry. Less load-bearing now that the
shared-anchor and small-content patterns are exercised, but a deliberate
small-cap pick from a different sector (e.g., a regional bank or a
single-product manufacturer) would broaden coverage further.

---

## How to add a fixture

1. Use [`eval/probe_new_fixtures.py`](../probe_new_fixtures.py) to find
   the canonical accession for a (CIK, target_year) pair. Older years
   live in `data['filings']['files']`, not `recent` — the probe walks
   both.
2. Append the JSONL line to [`filings.jsonl`](filings.jsonl).
3. Inspect the actual filing content with
   [`../inspect_filing.py`](../inspect_filing.py) to see what items have
   non-extracted statuses; only add `expected_status_overrides` entries
   you can verify from the filing's text — never from the pipeline's output.
4. Run [`../run_eval.py`](../run_eval.py) and document the new fixture
   here, including any bug it surfaces.
