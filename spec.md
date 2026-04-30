# spec.md — Task 3: SEC 10-K Item-level Structured Extraction

## 1. Background & framing

The interview prompt asks for a pipeline that takes a 10-K (by `CIK + accession_number` or by `file_url`) and produces structured JSON, one record per Item, with a `status` field that distinguishes real content from `incorporated_by_reference` / `not_applicable` / `reserved`. The grader will call our deployed Zeabur service with their own selected filings.

**Key framing — do not lose this:**

- 10-Ks have a **fixed item catalog** (Items 1–16 across Parts I–IV) but the **rendering varies enormously**: modern inline-XBRL HTML with TOC anchors, mid-2000s loose HTML, pre-2002 plain-text `.txt` filings, 10-K/A amendments that re-state only some items.
- "Status" is **not** a structural property — it is a textual pattern the registrant chose to write inside an otherwise-present item. A perfectly-located Item 10 can still be `incorporated_by_reference`; an Item 6 with three lines of "Reserved" text is `reserved`. Status detection must run **per item, after locating**.
- `char_range` is only meaningful relative to a stable, single-pass normalization of the document. We define it as offsets into the **normalized plain text** (post-HTML-strip, post-whitespace-collapse), not the raw HTML. The normalized text is part of the response (or addressable by URL) so grader can verify ranges.
- "Verify yourself without public ground truth" is a real constraint. Our weapons: (a) cross-strategy agreement, (b) char-range continuity / non-overlap, (c) item-number monotonicity, (d) XBRL Company Facts as an independent signal that financial statements (Item 8) actually contain numbers.
- This is a **cost-disciplined system**. The rule of thumb: if a 1996 plain-text filing and a 2024 inline-XBRL filing both cost the same number of LLM tokens to parse, we have built the wrong system. Rules-first is non-negotiable.

The four scoring axes from the prompt, re-interpreted:

| Axis | What it means here |
|---|---|
| Eval design depth | Eval set deliberately includes old plain-text, Part III incorporation-heavy, "Not Applicable" / "Reserved" cases, and 10-K/A amendments — not just clean modern filings. |
| Parsing strategy tradeoffs | Rules (TOC anchors → heading regex) handle the easy 70-90%; LLM is fallback on residuals. Cost and latency reported per request. |
| Self-verification w/o ground truth | Cross-strategy vote, char-range continuity, item-number monotonicity, XBRL cross-check. Disagreements surface as `warnings`. |
| Incorporated-by-reference handling | Detected by textual pattern + brevity; surfaced as a distinct status, not silently skipped. |

## 2. Goals

- G1. A deployed API on Zeabur that, given `(CIK + accession_number)` or a `file_url`, returns structured JSON conforming to §4.3 within 90 seconds for a typical modern filing.
- G2. Hybrid extraction with **explicit reporting of which strategy resolved each item** (`toc` / `heading` / `llm`), measured per-request and rolled up across the eval set.
- G3. Per-item `status` correctly distinguishes the four cases on the eval set's hand-labeled spot checks.
- G4. Eval set of ≥ 12 filings spanning industries, years, sizes, and rendering generations; eval harness reports accuracy + per-failure-mode breakdown + cost/latency.
- G5. README documents the parsing strategy ladder, the self-verification mechanism, honest failure modes, and how AI shaped the design.
- G6. `prompts/` shows AI collaboration where it actually moved the design — not transcripts of code generation.

## 3. Non-goals (cut for the time budget)

- **Fund / non-corporate filings.** Out of scope: 10-K/A for trusts, BDCs that file weird hybrids. Standard operating-company 10-Ks only.
- **Foreign filings (20-F, 40-F).** Different item catalogs entirely.
- **Inline-XBRL fact extraction.** We use Company Facts API for cross-validation only, not as a feature.
- **Diff against prior-year filing.** Not part of the prompt.
- **A UI.** API only, with a single `/healthz` and `/extract` endpoint. (Optional: minimal HTML form for grader convenience.)
- **Authentication on the demo API.** SEC's own data is public; the API allowlist is on outbound calls (only `*.sec.gov`).
- **Persistence.** Filing-level cache to disk only; no database.

## 4. Functional requirements

### 4.1 Item catalog

The canonical 10-K item set as of FY 2023+ (includes Item 1C cybersecurity, added 2023; Item 9C foreign-jurisdictions, added 2021):

| Part | Item | Title (canonical) |
|---|---|---|
| I | 1 | Business |
| I | 1A | Risk Factors |
| I | 1B | Unresolved Staff Comments |
| I | 1C | Cybersecurity (added 2023) |
| I | 2 | Properties |
| I | 3 | Legal Proceedings |
| I | 4 | Mine Safety Disclosures |
| II | 5 | Market for Registrant's Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities |
| II | 6 | [Reserved] (was Selected Financial Data, eliminated 2021) |
| II | 7 | Management's Discussion and Analysis of Financial Condition and Results of Operations |
| II | 7A | Quantitative and Qualitative Disclosures About Market Risk |
| II | 8 | Financial Statements and Supplementary Data |
| II | 9 | Changes in and Disagreements with Accountants on Accounting and Financial Disclosure |
| II | 9A | Controls and Procedures |
| II | 9B | Other Information |
| II | 9C | Disclosure Regarding Foreign Jurisdictions That Prevent Inspections (added 2021, HFCAA) |
| III | 10 | Directors, Executive Officers and Corporate Governance |
| III | 11 | Executive Compensation |
| III | 12 | Security Ownership of Certain Beneficial Owners and Management and Related Stockholder Matters |
| III | 13 | Certain Relationships and Related Transactions, and Director Independence |
| III | 14 | Principal Accountant Fees and Services |
| IV | 15 | Exhibits, Financial Statement Schedules |
| IV | 16 | Form 10-K Summary |

The catalog lives in `extractor/canonical_items.py` and is the single source of truth for expected item numbers, titles, fuzzy-match aliases, and which filings era they apply to.

### 4.2 Input contract

`POST /extract` accepts EITHER:

```json
{ "cik": "320193", "accession_number": "0000320193-24-000123" }
```

OR:

```json
{ "file_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm" }
```

Validation:
- `cik`: 1–10 digits; left-padded to 10 internally.
- `accession_number`: format `NNNNNNNNNN-NN-NNNNNN` or no-dashes equivalent.
- `file_url`: must be on `www.sec.gov` or `data.sec.gov` (outbound allowlist).
- Mutually exclusive: at most one of the two pairs may be present.

### 4.3 Output contract

```json
{
  "filing": {
    "cik": "0000320193",
    "accession_number": "0000320193-24-000123",
    "form": "10-K",
    "filing_date": "2024-11-01",
    "period_of_report": "2024-09-28",
    "primary_document_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm",
    "company_name": "Apple Inc."
  },
  "items": [
    {
      "part": "I",
      "item_number": "1",
      "item_title": "Business",
      "content_text": "...",
      "char_range": { "start": 1234, "end": 56789 },
      "status": "extracted",
      "resolved_by": "toc"
    }
  ],
  "stats": {
    "items_total": 23,
    "items_extracted": 18,
    "items_incorporated_by_reference": 4,
    "items_not_applicable": 0,
    "items_reserved": 1,
    "items_missing": 0,
    "strategies": { "toc": 18, "heading": 3, "llm": 1 },
    "duration_ms": 4280,
    "fetch_ms": 850,
    "llm_calls": 1,
    "llm_input_tokens": 2400,
    "llm_output_tokens": 320,
    "estimated_cost_usd": 0.0042
  },
  "warnings": [
    { "code": "char_range_gap", "message": "...", "between_items": ["3", "4"] }
  ]
}
```

`items` is ordered by `(part, item_number)` using the canonical sort key (1, 1A, 1B, 1C, 2, …).

### 4.4 Status semantics

| Status | Trigger | Notes |
|---|---|---|
| `extracted` | Default. Section located AND content above brevity threshold AND no incorporation/NA/reserved pattern matched. | `content_text` is the full normalized text of the section. |
| `incorporated_by_reference` | Section located AND text matches "incorporated.{0,20}by.{0,20}reference" near the start (within first 500 chars) AND content below ~1500 chars OR contains DEF 14A / proxy statement reference. | Common in Items 10–14. `content_text` retains the brief reference text. |
| `not_applicable` | Section located AND content < 300 chars AND matches `not\s+applicable` or `none` (case-insensitive). | Common for Item 1B (unresolved comments), Item 4 (mine safety) outside mining. |
| `reserved` | Section located AND content < 200 chars AND matches `\[?reserved\]?`. | Item 6 is always `reserved` post-2021. |

A separate `items_missing` counter (NOT a status) tracks items that the locator couldn't find at all — these don't appear in `items[]` but are reported in `stats`. This forces the system to be honest when it fails.

### 4.5 Pipeline stages

```
1. Resolver       — (cik+accession) | file_url → primary 10-K document URL
2. Fetcher        — HTTP GET with User-Agent + 10 req/sec rate limit + on-disk cache
3. Format detect  — html_modern | html_legacy | plain_text
4. Normalizer     — raw → normalized text + offset map; preserve heading boundaries
5. Item locator   — multi-strategy:
      A. TOC anchors  (most reliable on modern filings)
      B. Heading regex (\bItem\s+(\d+[A-Z]?)\b near line start, sequential)
      C. LLM fallback (only on residuals, max once per request)
6. Status detect  — per-item textual classifier (rules-only, no LLM)
7. Validator      — order monotonicity, char_range non-overlap, canonical title match,
                    Item 8 ↔ XBRL Company Facts cross-check
8. Output         — assemble + emit warnings
```

Each stage is independently testable. The LLM fallback in step 5C is **the only LLM call by default**. Status detection (step 6) is rules-only because the patterns are tight and we want this to be cheap and deterministic.

### 4.6 Self-verification (no public ground truth)

We treat these as `warnings` — they don't fail the request, but they signal trouble:

| Check | Failure signal |
|---|---|
| Item-number monotonicity | `1, 1A, 1B, 2, 3, 5` — Item 4 missing when filer is non-mining is suspicious only if neighbours are present. |
| char_range non-overlap | Adjacent items must satisfy `items[i].end ≤ items[i+1].start`. |
| char_range coverage | Sum of item ranges should cover ≥ 60% of normalized doc (the rest is TOC, signatures, exhibits index). |
| Canonical title fuzzy match | Located item title vs canonical, Levenshtein ratio ≥ 0.8. |
| Item 8 ↔ XBRL | If Item 8 status == `extracted`, Company Facts API must return ≥ 1 fact. |
| Strategy disagreement | If TOC and heading regex give different boundaries for the same item, prefer TOC and emit warning. |
| Brevity sanity | `extracted` Item 1 (Business) under 1000 chars is suspect (unless small REIT / shell co). |

### 4.7 Auth & safety

- **Outbound allowlist**: only `www.sec.gov`, `data.sec.gov`, `efts.sec.gov`. Enforced at the fetcher level.
- **SEC etiquette**: `User-Agent: SEC-10K-Extractor (contact: <email>)` set from env; 10 req/sec hard cap via token-bucket; back-off on 429.
- **No inbound auth on the demo**. Public SEC data; no secrets exposed; the cap is the rate limiter and request-size limit.
- **LLM key**: `ANTHROPIC_API_KEY` (Haiku for status sanity checks if ever needed; Sonnet for the rare LLM locator fallback). Mutually exclusive with `CLAUDE_CODE_OAUTH_TOKEN`.
- **Request limits**: max document size 10 MB (rejects pathological inputs); 90 s timeout.

### 4.8 API surface

- `POST /extract` — body per §4.2; response per §4.3. Synchronous; 90 s timeout.
- `GET /extract/{cik}/{accession}` — convenience GET form, same response.
- `GET /healthz` — 200 if process is up and outbound to `data.sec.gov` succeeds in last 60 s.
- `GET /` — minimal HTML form (CIK + accession inputs) that POSTs to `/extract` and pretty-prints JSON. Grader convenience only.
- `GET /eval/last` — returns the most recent eval run's summary JSON. (Read-only; no auth required since eval data is non-sensitive.)

## 5. Eval requirements

### 5.1 Eval set composition

`eval/fixtures/filings.jsonl` ≥ 12 entries, each:

```json
{
  "cik": "320193",
  "accession_number": "0000320193-24-000123",
  "label": "AAPL FY24 — modern, clean baseline",
  "category": "modern_clean | incorporation_heavy | plain_text | amendment | small_cap | new_items_2023 | mining | bank",
  "expected_items_present": ["1", "1A", "1B", "1C", "2", ...],
  "expected_status_overrides": { "6": "reserved", "10": "incorporated_by_reference" }
}
```

Category targets (≥ 12 filings total, with overlap allowed):

- ≥ 2 `modern_clean` (e.g., AAPL, MSFT recent)
- ≥ 2 `incorporation_heavy` (Items 10-14 incorporated by reference — common pattern)
- ≥ 1 `plain_text` (pre-2002 `.txt` filing)
- ≥ 1 `amendment` (10-K/A)
- ≥ 1 `small_cap` (micro/small-cap to stress non-canonical formatting)
- ≥ 1 `new_items_2023` (FY 2023 or later, must cover Item 1C)
- ≥ 1 `mining` (Item 4 actually populated)
- ≥ 1 `bank` (Item 8 large; Industry Guide 3 disclosures)

Labels are hand-curated from the filings' own TOCs. We don't claim to have ground truth on `content_text` — only on which items should be present and which non-`extracted` statuses are expected.

### 5.2 Eval harness

`eval/run_eval.py`:

1. For each fixture, call `/extract` against the live (or local) URL.
2. Compute per-fixture metrics:
   - `items_recall` = found items ∩ expected items / expected items
   - `status_correctness` = correctly-classified statuses / fixtures with expected overrides
   - `strategy_distribution` = % of items resolved by toc / heading / llm
   - `latency_ms`, `cost_usd`
3. Compute aggregate metrics + per-category breakdown.
4. Write `eval/results/<timestamp>.md` with:
   - Headline numbers
   - Per-fixture pass/fail
   - Per-category breakdown
   - Per-failure-mode list (which fixtures hit which `warnings`)
   - Cost / latency distribution

Pass bar (the harness exits non-zero below these):

- `items_recall` ≥ 0.90 across the eval set
- `status_correctness` ≥ 0.85 on fixtures with expected overrides
- p95 latency ≤ 30 s on `modern_clean`

## 6. Acceptance criteria

- [ ] Pipeline stages each have a unit test with ≥ 1 fixture.
- [ ] `/extract` end-to-end against AAPL most recent 10-K returns the canonical 23-item set with `items_missing == 0` (allowing 1B/4 to be `not_applicable`).
- [ ] `/extract` against a filing with extensive Part III incorporation correctly emits `incorporated_by_reference` for Items 10–14.
- [ ] `/extract` against a 1990s plain-text filing returns ≥ 80% items_recall.
- [ ] `eval/run_eval.py` runs to completion against the live URL; report exists with all sections populated.
- [ ] README documents architecture, the strategy ladder, ≥ 3 honest failure modes, the cost discipline, and the live URL.
- [ ] `prompts/` ≥ 3 substantive entries that reshape the design (not transcripts of generation).
- [ ] Service reachable on Zeabur; `/healthz` 200; outbound to `data.sec.gov` succeeds.
- [ ] ≥ 8 commits with intent-revealing messages.

## 7. Out of scope (explicit)

- Fund / trust / BDC filings; non-`10-K` forms.
- 20-F / 40-F (foreign).
- Database persistence (file cache only).
- Fact-level XBRL extraction (we only check for *presence* of facts as cross-validation).
- Multi-tenancy, request authn, billing.
- Streaming responses; incremental output.
- A frontend beyond the stub HTML form.
