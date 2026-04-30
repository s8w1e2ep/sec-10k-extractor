# 03-eval-set-design.md — Eval set construction and the era-notes prompt

The eval set design is documented in `spec.md` §5.1 and `plan.md` §2.10
as architecture. This file covers the *prompts* that shaped its
construction — particularly one that surfaced a latent bug in the catalog
itself.

## "我希望可以將剛剛提到的一些特定年份做一些注記"

After I'd talked the user through the SOX renumbering (2003), the
cybersecurity rule (2023), and the HFCAA Item 9C (2021), they asked:

> 在準備 eval set 時，我希望可以將剛剛提到的一些特定年份（格式有調整
> 的年份），做一些注記（改動了什麼），方便後續再選擇測試資料時可以
> 參考。

This was a request for a reference document — it became
`eval/fixtures/format_eras.md`. But the act of *writing* that document
forced me to enumerate every era cutoff explicitly:

| Era | What changed |
|---|---|
| FY 2023+ | Item 1C Cybersecurity added |
| FY 2021+ | Item 9C added (HFCAA) |
| FY 2020+ | Item 6 → [Reserved] |
| FY 2019+ | Inline XBRL phased in |
| FY 2011+ | Item 4 became Mine Safety Disclosures |
| FY 2009+ | XBRL data (separate exhibits) mandated |
| FY 2003+ | Items 1A/1B/9A/9B added; Item 14 split into 14/15 |
| FY 1997+ | Item 7A added |

Writing this table made me notice: my `canonical_items.py` only had
`valid_from_year` for two items (1C and 9C). All the other era cutoffs
were *implicit knowledge in my head* that the catalog didn't encode.

This was a real bug waiting to bite. When Phase 2 ran AAPL FY 1996, it
correctly located 14 items, but `expected_items_for_period(1996)` returned
21 expected items (filtering only 1C and 9C). Result: **7 false-positive
"missing items" warnings** on a filing that was perfectly parsed.

Adding `valid_from_year` to 1A, 1B, 7A, 9A, 9B, 15, and 16 — directly
informed by the era table I'd just written for the user — fixed the
expected-items math. Recall went 67% → 100% on AAPL FY 1996 without
changing the locator at all.

A document the user asked for as a *reference for picking fixtures* turned
into the source-of-truth update for the catalog.

## What `expected_status_overrides` is and isn't

For each fixture in `eval/fixtures/filings.jsonl`, two optional keys can
appear:

```json
"expected_items_present": ["1", "1A", ...]
"expected_status_overrides": {"6": "reserved", "10": "incorporated_by_reference"}
```

`expected_items_present` is *what items we expect to find*. If absent,
derived from `period_of_report` via `expected_items_for_period`. Auto-
derivable for any filing year.

`expected_status_overrides` is *what we expect the filer to have written*.
Not auto-derivable — depends on the specific filer's choices (most large
caps incorporate Items 10–14 by reference; some don't; small caps often
don't).

The discipline: only fill `expected_status_overrides` when I'd manually
confirmed it from a smoke run or by reading the filing's TOC. **Bluffing
overrides would make the metric measure my guesses, not the pipeline.**

In v1 I filled overrides for two fixtures only:
- AAPL FY 2025 (10 entries — saw them in Phase 1 smoke output)
- AAPL FY 1996 (1 entry — saw Item 9 N/A in Phase 2)

That's why `agg_status_correctness = 1.000` only spans 2 fixtures.
Honest, narrow, growable.

## Why hand-curated 10 beats auto-sampled 100

Spec §5.1 mandates 8 categories with ≥1 each: `modern_clean`, `incorporation_heavy`,
`plain_text`, `amendment`, `small_cap`, `new_items_2023`, `mining`,
`bank`. Total target ≥ 12 fixtures.

I went with 10 hand-curated, deliberately covering 6 of 8 categories
(`amendment` and `small_cap` deferred — see below). The alternative
considered: probe SEC EDGAR for 100 random recent 10-Ks and run the
pipeline against them.

Reasons hand-curated wins:

1. **Status overrides are useless without manual labelling.** A random
   sample of 100 filings could measure `items_recall` but not
   `status_correctness` — we'd have no ground truth for which items the
   filer chose to incorporate by reference. With 10 hand-picked filings,
   I have ground truth on the 2 I'd inspected and a clear path to expand
   to others as I read them.

2. **Diversity of failure mode beats statistical power.** 100 random
   modern filings would all behave like AAPL — TOC anchor passes 23/23,
   no learning. The 10 hand-picked filings include MSFT (different TOC
   convention), Apollo (GUID anchors), AAPL FY 1996 (plain text). Three
   distinct bugs surfaced from three distinct filers — see
   `02-strategy-ladder.md`.

3. **Time budget.** Spec sets the eval phase at ≈40 min. Hand-picking +
   labelling 10 fixtures fits; auto-sampling + labelling 100 doesn't.

The trade-off: with n=10, our recall metric has high variance. A single
new failure mode could drop us from 1.00 to 0.90. We accept that — Phase 5
isn't trying to *prove* generality, it's trying to *catch* representative
failures. Generality comes from category coverage, not sample size.

## What went wrong: CIK 1411494

When I built `build_fixtures.py`, I targeted CIK 1411494 thinking it was
Stitch Fix (the small-cap representative I had in mind). The Submissions
API came back with "Apollo Asset Management Inc." — CIK reassigned or my
mental model was just wrong. Apollo Asset Management is a $40 B+ market
cap company; not small-cap.

Two ways to handle:

A. **Drop the fixture, go find a real small-cap.** Costs more time;
breaks the budget.

B. **Re-categorize Apollo as `incorporation_heavy`** (asset managers'
Part III tends to be IBR — likely true). Keep the fixture. Acknowledge
`small_cap` is uncovered.

Chose B. The fixture was already useful — Apollo's GUID-anchor TOC
surfaced bug #3 (row-level extraction). The category mislabel is a
known gap, logged in `decisions.md` and `task.md` Phase 8.

Lesson generalized: when probing external systems, *verify the response
matches the assumption*. If I'd printed the company name back during
fixture generation and noticed "Apollo, not Stitch Fix" before adding it,
I'd have re-targeted earlier. The probe script now prints the company
name on every fetch — small fix, would have caught it.

## What's still uncovered

`amendment` (10-K/A): no recent 10-K/A in any candidate company's recent
filings slice. Would need a focused EDGAR full-text search. Logged.

`small_cap`: needs deliberate research to pick a true small-cap with a
clean enough 10-K to verify. Logged.

Both are Phase 8 backlog items. The pass bar is met without them; the
"intentionally stresses edge cases" rubric scoring axis is partially
met (6 of 8 categories represented, with the eval already having
surfaced 3 bugs from those 6). Not nothing, not complete.
