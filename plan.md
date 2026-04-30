# plan.md — Implementation plan

## 1. Architecture

```
       ┌──────────────────────────────────────────────┐
       │  Grader / curl                               │
       └────────────────┬─────────────────────────────┘
                        │ POST /extract
                        │   { cik + accession_number }  OR
                        │   { file_url }
                        ▼
       ┌──────────────────────────────────────────────┐
       │  FastAPI (server/main.py)                    │
       │  ─ validate input                            │
       │  ─ pipeline.run(input) ──────────────────────┐
       │  ─ assemble response, return                 │
       └──────────────────────────────────────────────┘
                                                      │
       ┌──────────────────────────────────────────────┘
       ▼
       ┌──────────────────────────────────────────────┐
       │  Pipeline (extractor/pipeline.py)            │
       │                                              │
       │  1. resolver        → primary 10-K URL       │
       │       (Submissions API if accession given)   │
       │  2. fetcher         → bytes (with cache)     │
       │       (User-Agent, 10 req/s, on-disk cache)  │
       │  3. format_detect   → html_modern | html_legacy | plain_text
       │  4. normalizer      → text + offset_map      │
       │  5. locator         → list[ItemSpan]         │
       │       A. toc_anchor                          │
       │       B. heading_regex                       │
       │       C. llm_fallback (Sonnet, residuals)    │
       │  6. status_detect   → per-item rules         │
       │  7. validator       → warnings[]             │
       │       (incl. XBRL Company Facts cross-check) │
       │  8. assemble        → output dict            │
       └──────────────────────────────────────────────┘
                                                      │
       ┌──────────────────────────────────────────────┘
       ▼
       ┌──────────────────────────────────────────────┐
       │  External:                                   │
       │   data.sec.gov (Submissions, Company Facts)  │
       │   www.sec.gov  (raw filings)                 │
       │   efts.sec.gov (full-text search; optional)  │
       │   api.anthropic.com (Sonnet, fallback only)  │
       └──────────────────────────────────────────────┘
```

**Key choice: rules-first locator with LLM as a fenced last resort.** The system is designed so that on a clean modern filing, **zero LLM calls** are made. The LLM is invoked only when both TOC anchors and heading regex have residual gaps, and only on the residual character ranges (not the whole document).

## 2. Components

### 2.1 `extractor/canonical_items.py`

Single source of truth for the 22-item catalog (§4.1 of spec). Provides:

- `CANONICAL_ITEMS: list[CanonicalItem]` — `(part, item_number, title, aliases, valid_from, valid_to)`.
- `sort_key(item_number) → tuple` — stable ordering across `1, 1A, 1B, 1C, 2`.
- `expected_items_for_period(period_of_report: date) → list[CanonicalItem]` — filters out 1C / 9C for filings before their introduction; includes Item 6 as `[Reserved]` from 2021 onward.
- `fuzzy_match_title(found: str) → CanonicalItem | None` — Levenshtein-based match used by the validator.

### 2.2 `extractor/fetcher.py`

- `fetch(url: str) → bytes` with on-disk cache at `cache/<sha256(url)>.bin`.
- Token-bucket rate limiter: 10 req/s (SEC ceiling).
- `User-Agent: SEC-10K-Extractor/<version> (contact: <SEC_CONTACT_EMAIL env>)`. SEC requires this; missing UA → 403.
- Retry on 429 / 5xx with exponential backoff (base 1 s, max 30 s, max 4 retries).
- Outbound allowlist: `*.sec.gov` only.

### 2.3 `extractor/resolver.py`

Two paths:

- **(cik, accession) path**: GET `https://data.sec.gov/submissions/CIK{cik:0>10}.json` → find row in `recent` (or `files/CIK{cik}-submissions-001.json` for older accessions) where `accessionNumber == accession`. Read `primaryDocument`. Construct `https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{primary_document}`.
- **file_url path**: validate it's on the allowlist, fetch directly. CIK + accession parsed back from URL path for the `filing` block of the response.

### 2.4 `extractor/format_detect.py`

Heuristics, in order:

- Content-Type `text/html` AND has `<ix:` namespace → `html_modern` (post-2009 inline XBRL).
- Content-Type `text/html` AND no `<ix:` → `html_legacy`.
- Else (`text/plain`, or `.txt` URL) → `plain_text`.

The output is a tag, not a parser choice — the normalizer branches internally. We log it so the eval can correlate strategy-distribution with format.

### 2.5 `extractor/normalizer.py`

HTML path (BeautifulSoup):

- Walk DOM, emit text with offset tracking. Preserve heading-like boundaries: `<h1>..<h6>`, `<b>` / `<strong>` that span their entire parent block, `<table>` cells whose only child is bold-font text.
- Strip `<script>`, `<style>`, inline XBRL `<ix:nonNumeric>` *wrappers* (keep their text).
- Collapse runs of whitespace to single spaces, `\n\n+` → `\n\n`.
- Output: `NormalizedDoc(text: str, headings: list[Heading], anchors: list[Anchor], format: str)`.
  - `headings`: `(level, text, char_offset)`.
  - `anchors`: extracted from `<a name>` / `id` attributes — the TOC links to these.

Plain-text path:

- Detect form-feeds (`\f`) as page breaks (common in EDGAR `.txt`).
- Detect heading lines: short lines (< 80 chars) in ALL CAPS or starting with `Item N`, surrounded by blank lines.
- Same `NormalizedDoc` shape.

### 2.6 `extractor/locator.py`

Three strategies, run in order; each returns a `list[ItemSpan]` (which may be partial):

**A. `toc_anchor`**

- Find the TOC: anchor links whose text matches `Item\s+\d+[A-Z]?` and which point to in-document `id`s.
- The TOC entries define the item starts. Each item's end is the start of the next TOC entry.
- Robust on modern filings (90%+ of post-2010); weak on legacy HTML and plain text.

**B. `heading_regex`**

- Scan headings (or, for plain text, lines that look like headings).
- Pattern: `^\s*(?:Item|ITEM)\s+(\d{1,2})([A-Z]?)\.?\s*[—–-]?\s*(.{0,200})$`.
- Require monotonic ordering — out-of-order matches are TOC echoes, dropped.
- The end of item N is `start of item N+1 - 1`, or end-of-doc for the last.
- Catches what TOC misses; runs on every filing as a sanity check even when TOC succeeded.

**C. `llm_fallback`**

- Triggered when items are missing after A and B AND the gap between the last located item and the next is > 5000 chars (i.e., there's content there but neither rule strategy claimed it).
- Prompt: send the residual character range + the list of missing item numbers + their canonical titles. Ask Claude (Sonnet) to identify start offsets for the missing items.
- Cap: max 1 LLM call per request, max 50 KB sent. Cost recorded.
- If LLM returns nothing usable, items stay in `items_missing`.

Strategies vote — if A and B disagree on item N's start by > 200 chars, the warning fires and A wins (TOC is more reliable on modern filings).

### 2.7 `extractor/status_detect.py`

Pure rules, no LLM. Operates on `(item, item.content_text)`:

```python
def detect_status(item_number: str, text: str) -> Status:
    head = text[:1500].lower().strip()

    if len(text) < 200 and "reserved" in head:
        return "reserved"

    if len(text) < 300 and re.search(r"\bnot\s+applicable\b|^none\b", head):
        return "not_applicable"

    if re.search(r"incorporat\w+\s+(here)?in?\s*by\s+reference", head):
        if len(text) < 1500 or re.search(r"def\s*14a|proxy\s+statement", head):
            return "incorporated_by_reference"

    return "extracted"
```

Tuned on the eval set; thresholds documented inline.

### 2.8 `extractor/validator.py`

Runs all checks from spec §4.6, returns `list[Warning]`. Specifically:

- Item-number monotonicity (per Part).
- Char-range non-overlap.
- Coverage: `sum(end - start) / len(normalized_text)` — warn if < 0.5.
- Canonical title fuzzy match.
- XBRL Company Facts cross-check: GET `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:0>10}.json`. If 404 → warn (no XBRL filed). If 200 and Item 8 status is `extracted` → require ≥ 1 fact in `facts.us-gaap.*`.
- Strategy disagreement (recorded by locator, surfaced here).

### 2.9 `server/main.py`

FastAPI:

- `POST /extract` — body validated against `ExtractRequest` Pydantic model; calls `pipeline.run`; returns `ExtractResponse`. 90 s timeout via `asyncio.wait_for`.
- `GET /extract/{cik}/{accession}` — same handler, args from path.
- `GET /healthz` — checks last-successful outbound to `data.sec.gov` (cached in process memory).
- `GET /` — single-file HTML stub form for grader convenience.
- `GET /eval/last` — reads `eval/results/_latest.json` if present.

Single worker for v1 (so the rate limiter is genuinely 10/s globally; multi-worker would need shared state).

### 2.10 Eval harness (`eval/run_eval.py`)

Pure stdlib + `httpx`. Reads `eval/fixtures/filings.jsonl`, posts each to `/extract`, computes metrics from §5.2, writes both `eval/results/<timestamp>.md` (human report) and `eval/results/_latest.json` (machine — for the `/eval/last` endpoint). Non-zero exit if pass-bar fails.

### 2.11 Deploy (`Dockerfile`, `zeabur.json`)

- `python:3.12-slim` base; install `lxml`, `beautifulsoup4`, `httpx`, `fastapi`, `uvicorn`, `python-Levenshtein`, `anthropic`.
- Cache directory volume-mounted (or persisted under `/data` if Zeabur supports it).
- Env vars: `SEC_CONTACT_EMAIL` (required), `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`, `LLM_FALLBACK_ENABLED` (default `true`).

## 3. Trade-offs

| Decision | Alternative | Why |
|---|---|---|
| Rules-first; LLM as last resort, capped at 1 call | LLM-first or LLM-always | Cost discipline. A clean modern filing should cost $0 in LLM. The LLM exists to handle the long tail, not the head. |
| `char_range` indexed against normalized text | Raw HTML offsets | Stability across reruns, usable by the grader without parsing HTML. The price: the grader needs the normalized text to verify; we expose it via response (or addressable URL). |
| Status detector is rules-only | LLM-classified status | The patterns (`incorporated by reference`, `not applicable`, `reserved`) are extremely tight. LLM would add cost + latency for ~0 accuracy gain. |
| Single-strategy winner per item, with disagreement → warning | Cross-strategy ensemble averaging | Boundaries are integer offsets; averaging is meaningless. Vote + warn is honest. |
| TOC anchor preferred over heading regex on disagreement | Heading regex preferred | TOC is what the filer themselves labelled; heading regex can match in-body italicised "Item 1A risk" references. |
| In-process token-bucket rate limit; single worker | Redis-backed limit; multi-worker | Single tenant, single demo; complexity not justified. Documented as scale-out item. |
| File-system cache keyed by URL hash | No cache / DB cache | Reruns during eval are ~free; survives container restarts (volume-mounted); no schema. |
| XBRL Company Facts cross-check is a `warning`, not a hard validator | Hard-fail on disagreement | XBRL ≠ Item 8 verbatim. Many small filers don't tag everything. Warning surfaces the smell without false-failing the request. |
| Synchronous `/extract` with 90 s timeout | Async + polling | Most filings finish in 2-15 s; 90 s catches the worst plain-text + LLM-fallback case; async would add complexity. |
| Eval set hand-curated to 12+ filings | Auto-sampled 100 random filings | 100 filings × manual labelling is out of budget. 12 deliberately-stressed cases beat 100 random. |
| `items_missing` is a counter, not a status | `status: missing` | `status` is what the *filer* declared; "missing" is what *we* failed to find. Conflating them hides our own errors. |

## 4. Risks

- **R1: Old filings break the parser.** Pre-2002 plain text is wildly varied; 80% recall is the v1 bar, 100% is wishful. Mitigation: dedicated `plain_text` branch in normalizer; eval set includes ≥ 1 plain-text fixture.
- **R2: TOC anchors don't exist on legacy HTML.** Heading regex is the fallback; LLM is the safety net.
- **R3: `incorporated_by_reference` false negatives.** A filer might phrase it as "see the Proxy Statement" without the literal "incorporated by reference" string. Mitigation: secondary pattern matching DEF 14A / proxy statement references combined with brevity.
- **R4: LLM fallback runs away on cost.** Mitigation: hard cap of 1 call/request, max 50 KB input, recorded per-request, surfaced in response stats.
- **R5: SEC blocks our User-Agent.** They rate-limit by UA. Mitigation: contact email in UA per their guidance; back-off + retry.
- **R6: 10-K/A amendments restate only some items.** Item set may be incomplete by design. Mitigation: validator warns on missing items but does NOT fail; README documents that 10-K/A is partial-by-design.
- **R7: Item 1C / 9C absence on older filings.** 1C is FY 2023+, 9C is FY 2021+. Pre-cutoff filings legitimately don't have these. Mitigation: `expected_items_for_period` filters by filing date so eval doesn't ding them; details in `eval/fixtures/format_eras.md`.
- **R8: Char-range drift.** Any change to the normalizer changes offsets. Mitigation: golden-file tests on AAPL FY24 with snapshot offsets; failures force a deliberate snapshot bump.
- **R9: 90 s timeout fires.** A 3 MB plain-text filing + LLM fallback could push it. Mitigation: format detection short-circuits LLM if doc > 1.5 MB normalized; document as known issue.

## 5. Sequence

1. **Skeleton + AAPL end-to-end (rules only).** Fetcher, resolver (Submissions API), normalizer, TOC-anchor locator, status detector, basic API. Goal: AAPL FY24 returns 22 items, statuses correct on the easy cases.
2. **Heading-regex locator + plain-text path.** Add the second strategy, branch in normalizer for `.txt` filings, add fixture for one pre-2002 filing.
3. **Validator + warnings.** Item monotonicity, char-range continuity, canonical title fuzzy-match, XBRL Company Facts cross-check.
4. **LLM fallback locator.** Sonnet, fenced, capped, cost-tracked.
5. **Eval set + harness.** ≥ 12 fixtures across the categories from spec §5.1; `run_eval.py`; first run; markdown report.
6. **README + `prompts/`.** Architecture, strategy ladder, ≥ 3 honest failure modes, AI-collaboration writeups.
7. **Zeabur deploy.** `Dockerfile`, `zeabur.json`, secret env vars, `/healthz` smoke, run live eval.

Detailed steps in `task.md`.
