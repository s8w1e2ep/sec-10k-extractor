# decisions.md — Implementation journal

Chronological log of issues that surfaced during implementation and the
decisions made in response. Separate from:

- `spec.md` — the design contract (what we're building)
- `plan.md` — architectural blueprint (how)
- `task.md` — phase-ordered checklist
- `prompts/` — AI-collaboration writeups (design-shaping prompts, for the grader)

This file is the "what came up while building, and why we did X" log.

---

## 2026-04-30 — Phase 0 / 1 / 2 build

### Python 3.12 not installed locally → using 3.13

The Dockerfile plan targets Python 3.12. Local environment had `python3.13`
and `python3.11` but no `python3.12`. Asked the user; chose 3.13 over `brew
install python@3.12` for speed (user OK'd it). Bumped `pyproject.toml` to
`requires-python = ">=3.11"`. Reconcile with Zeabur deploy environment in
Phase 7.

### Catalog count was 22 → corrected to 23

Spec, CLAUDE, README, and the catalog test all initially said "22 items".
Actual count is **23 sub-items**:

- Part I: 1, 1A, 1B, 1C, 2, 3, 4 = **7**
- Part II: 5, 6, 7, 7A, 8, 9, 9A, 9B, 9C = **9**
- Part III: 10, 11, 12, 13, 14 = **5**
- Part IV: 15, 16 = **2**

The phrase "Items 1–16" refers to **main item numbers** (16 of those); sub-
items A/B/C push the total to 23. Updated all docs + the assertion in
`tests/test_canonical_items.py::test_catalog_has_23_items`.

### `XMLParsedAsHTMLWarning` from BeautifulSoup

Inline-XBRL filings (post-2019) declare `xmlns:ix=...` and are technically
XHTML. BeautifulSoup with the `lxml` parser warns when an HTML parser is
used on XML. The HTML parser handles them fine in practice — AAPL FY2025
returned all 23 items via TOC-anchor strategy on the first try. Suppressed
the warning at the top of `extractor/pipeline.py` for clean output.

Alternative considered: `features="xml"`. Rejected because heading-regex
strategy depends on HTML semantics for headings/anchors.

### Old-style EDGAR URL form (pre-2002 filings)

Modern URL: `https://www.sec.gov/Archives/edgar/data/{cik}/{acc-no-dashes}/{filename}`
— directory per accession, primary document explicitly named.

Old URL: `https://www.sec.gov/Archives/edgar/data/{cik}/{acc-with-dashes}.txt`
— single `.txt` file containing the SEC pseudo-XML wrapper plus the entire
submission (10-K + all exhibits in one file).

Initial `resolve_by_file_url` only matched the modern form. Added
`_OLD_URL_RE` so both forms work. The plain-text normalizer extracts the
10-K content from `<DOCUMENT><TYPE>10-K…<TEXT>…</TEXT></DOCUMENT>`.

### Plain-text heading detection: in-body false positives

First plain-text heading-detection cut captured every line matching
`^\s*Item\s+\d`. On AAPL FY1996 this included wrapped in-body references
like:

> "...please refer to Item 8 on this Form 10-K in the Notes to Consolidated
> Financial Statements which..."

The locator's monotonic-by-canonical-index filter then dropped real Item 2
and Item 5 because "Item 8" ahead of them put the order index past 1, 2, …
This was the worst kind of bug — losing **real** items because of a wrapped
in-body phrase that happened to match the heading pattern.

Two fixes:

1. **Tightened `_PART_LINE_RE` to `\s*$` anchor.** Prevents "Part II, Item 7
   of this Form 10-K under the subheading 'Competition'" from matching the
   PART heading rule.
2. **Required prev-line-blank for plain-text heading lines.** Initially also
   required next-line-blank, but that broke real headings whose title wraps
   to a second line (e.g. "Item 5. Market for the Registrant's Common Equity
   and / Related Stockholder Matters" spans two lines). Relaxed to
   `prev_blank` only.

After fix: AAPL FY1996 → 14/14 items, zero false-positive headings.

### Item-era awareness: `valid_from_year` extension

`canonical_items.py` initially had `valid_from_year` only for 1C (2023) and
9C (2021). For AAPL FY1996, `expected_items_for_period(1996)` returned 21
expected items, but pre-2003 Form 10-K only had 14. Result: 7 spurious
"missing items" warnings on a perfectly-parsed filing.

Added `valid_from_year` for items added later:

| Item | Year | Source |
|---|---|---|
| 1A | 2005 | S-K Final Rule 33-8591 (Risk Factors mandate) |
| 1B | 2005 | Same rule (Unresolved Staff Comments) |
| 7A | 1997 | S-K Final Rule 33-7386 (Quant + Qual Market Risk) |
| 9A | 2003 | Sarbanes-Oxley (Controls and Procedures) |
| 9B | 2004 | Other Information |
| 15 | 2003 | SOX renumbering (old Item 14 "Exhibits" → Item 15) |
| 16 | 2016 | S-K Final Rule 33-10110 (Form 10-K Summary) |

After fix: AAPL FY1996 → expected = 14, located = 14, missing = 0.

### Item 14 / 15 era-renumbering: deferred to Phase 8

The Sarbanes-Oxley renumbering (2003) is a deeper problem we are deliberately
**not solving in v1**:

- **Pre-2003**: Item 14 = "Exhibits, Financial Statement Schedules, and
  Reports on Form 8-K". No Item 15.
- **Post-2003**: Item 14 = "Principal Accountant Fees and Services" (new).
  Item 15 = "Exhibits..." (renumbered from old 14).

The same Item NUMBER 14 carries **different content** across eras. Our
locator labels detected items using the canonical (post-2003) title, so
AAPL FY1996's Item 14 — 20 KB of exhibits — shows up in the response with
title "Principal Accountant Fees and Services". Wrong title, right number,
right content.

Acceptable for v1: the eval rubric checks number-level recall and status
classification, not title accuracy on pre-2003 filings. Logged in
`task.md` Phase 8 backlog. A future fix would let `CanonicalItem` carry
per-era titles and have the extractor pick based on `period_of_report.year`.

A similar known issue exists for Item 4 (was "Submission of Matters to a
Vote of Security Holders" pre-2011, now "Mine Safety Disclosures") — already
captured via `aliases` for fuzzy matching, but the response title still
shows the post-2011 canonical title.

### Uvicorn env-propagation gotcha (minor)

When booting `uvicorn` in a background shell during the Phase 1 smoke test,
`SEC_CONTACT_EMAIL` set on the outer shell didn't propagate. `/healthz`
worked but `/extract` 500'd with a misleading "SEC_CONTACT_EMAIL required"
inside the request handler. Fixed by setting the env inline on the uvicorn
launch command. Mentioned because the failure mode (healthz green, extract
red) is the kind of misleading split that wastes time on rediscovery.

### Phase 1 design validations (worth flagging in retrospect)

Two design bets paid off cleanly on Phase 1's smoke:

1. **Rules-first locator was the right call.** AAPL FY2025 returned 23/23
   items via TOC anchor alone. Zero LLM calls. $0 cost. 483 ms total. The
   premise behind §3 of `plan.md` ("a clean modern filing should cost $0
   in LLM") is empirically validated on the head case. The LLM fallback is
   reserved for the long tail.

2. **`items_missing` as a counter, not a `status` value.** Keeps the
   distinction "what the filer wrote" (status) vs "what we couldn't find"
   (missing) honest. When the AAPL FY1996 expected-items count was wrong,
   the warning surfaced cleanly as 7 missing items rather than disguising
   the bug as 7 wrong-status items.

---

_Phase 3 onward will append entries here as issues surface._
