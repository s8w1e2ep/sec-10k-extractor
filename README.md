# sec-10k-extractor

> Submission for **Task 3** of an AI Coding Test: SEC 10-K item-level structured extraction. Rules-first pipeline that takes a filing (by `CIK + accession_number` or `file_url`) and emits structured JSON across the canonical 10-K item set, distinguishing real content from `incorporated_by_reference` / `not_applicable` / `reserved` per item.

**Status**: shipped. Eval set passes all bars; rules carry the head, LLM (Claude Haiku 4.5) handles the long tail at ≤ 3 calls per request (shared budget across both LLM layers).

**Live URL**: <https://sec-10k.zeabur.app> ([`/healthz`](https://sec-10k.zeabur.app/healthz) · [HTML form](https://sec-10k.zeabur.app/))

---

## Quick start

### Local development

_Requires Python ≥ 3.11, `SEC_CONTACT_EMAIL` env var (SEC mandates a contact in the User-Agent header)._

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
SEC_CONTACT_EMAIL='you@example.com' .venv/bin/uvicorn server.main:app --reload --port 8000

# extract by CIK + accession (most reliable input form)
curl -X POST http://localhost:8000/extract \
  -H 'content-type: application/json' \
  -d '{"cik":"320193","accession_number":"0000320193-25-000079"}'

# extract by direct file URL (works for both modern and pre-2002 .txt filings)
curl -X POST http://localhost:8000/extract \
  -H 'content-type: application/json' \
  -d '{"file_url":"https://www.sec.gov/Archives/edgar/data/320193/0000320193-96-000023.txt"}'
```

Response shape and full I/O contract: [`spec.md` §4.3](./spec.md).

```bash
# Run the eval against a running server
.venv/bin/python eval/run_eval.py http://localhost:8000
# → eval/results/eval-<timestamp>.md
```

### Deploy (Zeabur via Dockerfile)

`Dockerfile` and `zeabur.json` are checked in. The image is `python:3.12-slim` + ca-certificates + the project; lxml ships manylinux wheels so no build deps are needed. Runs as a non-root `app` user; honours `$PORT` (Zeabur injects one).

Required env var on Zeabur:
- `SEC_CONTACT_EMAIL` — real email used in the SEC User-Agent header. SEC returns 403 without one.

Optional (set either one, not both — OAuth token is preferred when both are present):
- `CLAUDE_CODE_OAUTH_TOKEN` — long-lived OAuth credential issued by `claude setup-token`. Sent as `Authorization: Bearer` with `anthropic-beta: oauth-2025-04-20`; the system prompt is auto-prefixed with the Claude Code identifier line as the API requires.
- `ANTHROPIC_API_KEY` — regular API key. Sent as `x-api-key`.

Either one enables the two LLM-backed fallbacks (status resolver + locator fallback, see Architecture below). Without a credential the pipeline runs rules-only and any unresolved validator warnings are surfaced honestly.

Health check: `GET /healthz`. Cache directory: `/app/cache` (gitignored; volume-mountable).

---

## API reference

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/extract` | Extract structured JSON from a 10-K. Body specifies the filing. |
| `GET` | `/extract/{cik}/{accession}` | Same as POST, with the inputs in the URL. Convenience for `curl` / browser. |
| `GET` | `/healthz` | Liveness probe. Returns `{"status":"ok"}`. |
| `GET` | `/` | Minimal HTML form for grader-facing manual testing. |

### `POST /extract` request body

Pydantic-validated. Provide **either** `(cik, accession_number)` **or** `file_url`, never both.

```jsonc
{
  "cik": "320193",                        // any digit string; auto-padded to 10 digits
  "accession_number": "0000320193-25-000079" // 18-digit form or dashed; both accepted
}
```

```jsonc
{
  // Alternative: a direct EDGAR Archives URL. Useful for pre-2002 plain-text filings
  // where the (cik, accession) lookup would still work but the URL skips the Submissions hop.
  "file_url": "https://www.sec.gov/Archives/edgar/data/320193/0000320193-96-000023.txt"
}
```

### Success response (HTTP 200)

Full I/O contract: [`spec.md` §4.3](./spec.md). Shortened example:

```jsonc
{
  "filing": {
    "cik": "0000320193",
    "accession_number": "0000320193-25-000079",
    "form": "10-K",
    "filing_date": "2025-10-31",
    "period_of_report": "2025-09-27",
    "primary_document_url": "https://www.sec.gov/Archives/edgar/data/320193/.../aapl-20250927.htm",
    "company_name": "Apple Inc."
  },
  "items": [
    {
      "part": "I",
      "item_number": "1",
      "item_title": "Business",
      "content_text": "Apple Inc. designs, manufactures...",
      "char_range": { "start": 12, "end": 56789 },   // offsets into the normalized text
      "status": "extracted",                          // extracted | incorporated_by_reference | not_applicable | reserved
      "resolved_by": "toc"                            // toc | heading | llm
    }
    // … 22 more items …
  ],
  "stats": {
    "items_total": 23,
    "items_extracted": 13,
    "items_incorporated_by_reference": 5,
    "items_not_applicable": 4,
    "items_reserved": 1,
    "items_missing": 0,
    "strategies":  { "toc": 23, "heading": 0, "llm": 0 },
    "duration_ms": 1540,
    "fetch_ms": 169,
    "format": "html_modern",                          // html_modern | html_legacy | plain_text
    "llm_calls": 0,
    "llm_input_tokens": 0,
    "llm_output_tokens": 0,
    "estimated_cost_usd": 0.0
  },
  "warnings": [
    // self-verification signals; non-fatal. Empty array is the happy path.
    // See "Self-verification" section below for the full code list.
  ]
}
```

### Error responses

Every error has a structured JSON body. We never return bare 500s on **expected** failure modes.

| HTTP | Code path | Body shape |
|---|---|---|
| **400** | Malformed CIK / accession (caught by `_normalize_*`); unparseable `file_url`; outbound URL not on `*.sec.gov` allowlist | `{"detail": "Invalid CIK: 'abc'"}` |
| **400** | Non-10-K form (10-K/A amendment, 10-Q, 20-F, 8-K, …) — the form gate | `{"error": "Unsupported form: 10-K/A. This service supports 10-K only.", "form": "10-K/A", "supported_forms": "10-K (and historical 10-K family: 10-KSB, 10-K405, 10-KT). Amendments (/A suffix) are not supported."}` |
| **404** | CIK well-formed but doesn't exist on SEC; or CIK exists but accession not in its filings; or the document URL points to a non-existent file | `{"error": "CIK 9999999999 not found at SEC Submissions API", "what": "CIK 9999999999", "where": "SEC Submissions API"}` |
| **413** | Raw filing exceeds the 30 MB cap (rejected before BeautifulSoup is invoked, to protect the single-worker server from memory exhaustion) | `{"error": "Filing exceeds size limit: 35.2 MB > 30.0 MB", "size_bytes": 36909875, "limit_bytes": 31457280}` |
| **422** | Pydantic schema rejection: empty body, neither input form provided, or both forms provided at once | `{"detail": [{"type":"value_error", "msg":"Provide either (cik, accession_number) or file_url", ...}]}` |
| **502** | SEC EDGAR returned a 5xx that we couldn't recover from across the fetcher's 4 retries; or any other transient upstream issue | `{"error": "Upstream SEC Submissions API failed (status=503)", "upstream": "SEC Submissions API", "upstream_status": 503}` |
| **504** | The request exceeded the 90-second total timeout (cold cache + slow SEC + 1 LLM call should still fit comfortably; 504 means something unusual happened) | `{"detail": "Extraction exceeded 90s timeout"}` |

The 404 body's `what` field tells you exactly which input is wrong:

```jsonc
// CIK doesn't exist (fix the CIK):
{"error": "CIK 9999999999 not found at SEC Submissions API",
 "what": "CIK 9999999999", "where": "SEC Submissions API"}

// CIK exists but accession not found (fix the accession, CIK is fine):
{"error": "Accession 0000320193-99-999999 for CIK 0000320193 not found at SEC Submissions API",
 "what": "Accession 0000320193-99-999999 for CIK 0000320193", "where": "SEC Submissions API"}
```

### Hard limits and timeouts

| Constraint | Value | Where |
|---|---|---|
| Outbound request rate to SEC | 10 req/s | `extractor/fetcher.py` (in-process token bucket; that's why the server runs single-worker) |
| Max raw filing size | 30 MB | `extractor/pipeline.py:MAX_RAW_HTML_BYTES` |
| Max LLM call input | 50 KB | `extractor/llm_client.py:MAX_INPUT_CHARS` |
| Max LLM calls per request | 3 | `extractor/llm_client.py:MAX_LLM_CALLS_PER_REQUEST` (shared budget across Layer 1 + Layer 2; today each layer makes ≤ 1 call) |
| Daily LLM cost ceiling | $5 (default) | `extractor/llm_client.py:DAILY_LLM_BUDGET_USD` env; on exhaustion the request still succeeds with rules-only coverage and a `llm_skipped_daily_budget_exhausted` warning |
| Per-IP rate limit | 10 req/min, burst 10 | `server/main.py:rate_limit_middleware` (env: `RATE_LIMIT_PER_MIN` / `RATE_LIMIT_BURST`); `/healthz`, `/`, `/docs`, `/redoc`, `/openapi.json` exempt |
| Total request timeout | 90 s | `server/main.py` |

---

## What this is

10-Ks have a **fixed item catalog** (23 sections: Items 1–16 across Parts I–IV, with sub-items 1A/1B/1C/7A/9A/9B/9C) but the **rendering varies enormously**: modern inline-XBRL HTML with hyperlinked tables of contents, mid-2000s loose HTML, pre-2002 plain-text `.txt` filings, 10-K/A amendments. The grader calls `/extract` with their own selected filings; we return structured JSON.

**Per-item record**: `part`, `item_number`, `item_title`, `content_text`, `char_range`, `status`, `resolved_by`.

**Status** is a *textual property* the filer wrote inside an otherwise-present item — `extracted` / `incorporated_by_reference` / `not_applicable` / `reserved` — not a structural one. Status detection runs after locating, on the located content. An incorporated-by-reference Item 10 is still a *located* Item 10.

---

## Architecture

See [`plan.md` §1](./plan.md) for the diagram. Eight-stage pipeline:

```
1. Resolver       — (cik, accession) | file_url → primary 10-K URL via Submissions API
2. Fetcher        — User-Agent + 10 req/s token bucket + on-disk cache + *.sec.gov allowlist
3. Format detect  — html_modern | html_legacy | plain_text
4. Normalizer     — raw → text + offset map. char_range offsets are into this text.
5. Locator        — toc_anchor → heading_regex → (LLM fallback if items missing)
6. Status detect  — per-item rules-only classifier; LLM resolver re-runs when
                    validator flags title_mismatch on extracted items
7. Validator      — char-range geometry, brevity, monotonicity, title fuzzy-match,
                    XBRL Company Facts cross-check; emits warnings
8. Assemble       — output dict per spec §4.3
```

**Three locator strategies, layered**:

1. **`toc_anchor`** (most reliable on modern filings). Three patterns supported in v1:
   - Link text contains "Item N. Title" (AAPL convention)
   - Link text is just title; item number in href fragment (`#item_1_business` — MSFT/BRK convention)
   - TOC entry split across cells; row-level `<tr>`/`<li>` text matches "Item N. ..." (Apollo/Tesla convention)
2. **`heading_regex`** runs always, fills gaps TOC missed, votes on disagreement (TOC wins on conflict). Uses the canonical-index monotonicity filter to drop in-body cross-reference matches.
3. **`llm_fallback`** — fenced last resort. Fires when rules left required items unlocated; sends the first 50 KB of normalized text + the missing item numbers to Claude Haiku 4.5; LLM returns text snippets we then `text.find()` in the doc to recover offsets. Adds spans with `resolved_by="llm"`. On the current eval set this never triggers (rules are at 100% recall) — the path is wired and tested, waiting for a future weird filing to need it.

**LLM status resolver** (separate from the locator path): when `validator` emits `title_mismatch` on an `extracted` item, we ask the LLM to confirm or correct the status. Catches era-renames (pre-2003 Item 14 was today's Item 15 content) and unusual IBR phrasings (Berkshire-style "Except for the information set forth under the caption…") that pure rules miss. On the live eval this fires on 2 of 10 fixtures (BRK FY 2025, AAPL FY 1996) for a combined cost of **$0.0039**; the other 8 cost $0.

**Per-request LLM budget**: `MAX_LLM_CALLS_PER_REQUEST = 3` calls, shared across the locator fallback and the status resolver. Layer 2 (locator fallback) fires first when required items are missing; Layer 1 (status resolver) fires afterwards if the validator flagged `title_mismatch` and budget remains. Input is capped at 50 KB per call; cost lands in `stats.llm_calls` / `estimated_cost_usd`. Today each layer makes at most one call, so the realistic ceiling is 2 — the headroom to 3 leaves a slot for a future Layer 2 retry / chunked pass.

**Cost discipline**: the design contract (`spec.md` §1) is that "if a 1996 plain-text filing and a 2024 inline-XBRL filing both cost the same number of LLM tokens to parse, we have built the wrong system." A clean modern filing still costs $0; only the long-tail fixtures pay.

**Abuse prevention** (cost shields, not auth — see [`prompts/04-cost-shield.md`](./prompts/04-cost-shield.md)):

- **Per-IP rate limit** (`server/main.py:rate_limit_middleware`) — in-process token bucket keyed by `X-Forwarded-For` (Zeabur is behind a reverse proxy). Default 10 req/min burst 10, env-tunable. Returns `429 {"error":"rate limit exceeded"}` with `Retry-After: 1`. `/healthz`, `/`, `/docs`, `/redoc`, `/openapi.json` exempt so health probes don't flap.
- **Daily LLM cost ceiling** (`extractor/llm_client.py:daily_budget_remaining`) — process-wide accumulator, default $5/day, UTC date rollover. When exhausted, both LLM gates skip with a `llm_skipped_daily_budget_exhausted` warning; the request still returns 200 with rules-only coverage.
- **Forensic logging** — every request emits a structured JSON `http.request` line with `request_id`, `client_ip` (same source as the rate limiter), `status`, `duration_ms`. Pipeline emits `pipeline.start` / `pipeline.done`; fetcher emits `cache_hit` / `cache_miss` / `fetched` / `retry`. All three layers correlate via the contextvar-bound `request_id`.

These are **cost shields, not security boundaries** — the buckets and counter live in process memory and reset on container restart. Anyone willing to rotate IPs or wait for tomorrow's budget can still hit the API. If real auth becomes necessary, see option C in `prompts/04-cost-shield.md` (currently de-scoped).

---

## Self-verification (no public ground truth)

We don't have ground truth on `content_text`, so the validator cross-checks against independent signals (full list in [`spec.md` §4.6](./spec.md)). Each fires as a `warnings[]` entry; none fail the request.

| Check | Catches |
|---|---|
| `char_range_invalid` | end < start (combine_strategies edge case on out-of-order TOC) |
| `char_range_overlap` | adjacent items overlap |
| `low_coverage` | located items cover < 50% of normalized doc |
| `non_monotonic_order` | items in non-canonical order (sanity) |
| `suspect_brevity` | extracted Item 1 (Business) under 1000 chars |
| `title_mismatch` | heading text in content_text vs canonical (rapidfuzz partial_ratio < 75; aliases checked) |
| `xbrl_no_us_gaap` / `xbrl_not_filed` | Item 8 extracted but Company Facts API empty / 404 (skipped pre-2009) |

The design works as a self-correcting loop: the validator's `title_mismatch` is what triggers the LLM status resolver. AAPL FY 1996 Item 14 (pre-SOX "Exhibits…" content under the post-SOX canonical title) and BRK FY 2025 Item 14 (unusual IBR opener) both surface here; the LLM then either confirms the status (FY 1996 — content is real, status stays `extracted`) or corrects it (BRK — content was a forward-reference, status flips to `incorporated_by_reference`).

---

## Eval results

10 hand-curated fixtures. Latest live-deploy report (LLM enabled): [`eval/results/eval-20260430-170649.md`](./eval/results/eval-20260430-170649.md). Local rules-only baseline (no credential, cache-warm): [`eval/results/eval-20260430-171058.md`](./eval/results/eval-20260430-171058.md).

| Pass-bar check | Threshold | Local (rules-only, cache-warm) | Live (Zeabur, LLM enabled, cold) |
|---|---|---|---|
| `items_recall` | ≥ 0.90 | **1.000** | **1.000** |
| `status_correctness` | ≥ 0.85 | **1.000** (n=2 with overrides) | **1.000** (n=2) |
| p95 latency on `modern_clean` | ≤ 30 s | **1092 ms** | **4641 ms** |
| Total LLM calls | — | **0** | **2** |
| Total LLM cost | — | **$0.0000** | **$0.0039** |

The live numbers are slower because the Zeabur container starts with an empty fetcher cache (warms over re-runs) and the SEC fetch hops the public internet rather than localhost. Pass bar ~6× under threshold either way.

The 2 LLM calls on the live run are the status resolver firing on the same two `title_mismatch` warnings the rules-only run produced:
- BRK FY 2025 Item 14: rules locator placed the section correctly but the rules status detector marked it `extracted`. LLM confirms it's actually `incorporated_by_reference` (the filer's "Except for the information set forth under the caption…" opener is a forward-reference). **Status corrected.**
- AAPL FY 1996 Item 14: pre-SOX content (Exhibits and reports on Form 8-K) under post-SOX canonical title. LLM keeps `extracted` (content is real), confirming the title-mismatch is era-cosmetic, not a status bug.

Per-category coverage (six of eight categories from `spec.md` §5.1):

| Category | n | mean recall |
|---|---|---|
| `modern_clean` | 4 | 1.000 (AAPL, MSFT, NVDA, TSLA — all FY 2024+) |
| `incorporation_heavy` | 3 | 1.000 (BRK, WMT, Apollo) |
| `bank` | 1 | 1.000 (JPMorgan Chase) |
| `mining` | 1 | 1.000 (Newmont) |
| `new_items_2023` | 4 | 1.000 (overlap with modern_clean) |
| `plain_text` | 1 | 1.000 (AAPL FY 1996 .txt) |

The eval surfaced **three real bugs** that the AAPL-only smoke had silently passed for three phases:

1. href regex `\b` failed between digit and underscore — needed lookahead
2. MSFT/BRK style: link text is just the title, item number in href — needed href fallback
3. Apollo/Tesla style: TOC fragmented across cells/anchors — needed row-level extraction

Recall progression: **0.687 → 0.861 → 0.991 → 1.000** across four iterations. See [`prompts/02-strategy-ladder.md`](./prompts/02-strategy-ladder.md) for the full story.

---

## Honest failure modes

1. **Item 14 / 15 era-renumbering — title is still cosmetic.** Sarbanes-Oxley (2003) split pre-2003 "Item 14. Exhibits" into Item 14 (Principal Accountant Fees, new) and Item 15 (Exhibits, renumbered). Same NUMBER 14 carries different CONTENT across eras. AAPL FY 1996's Item 14 still emits as `item_title: "Principal Accountant Fees and Services"` because we resolve titles from the catalog, not from the filer's heading text. The status resolver (Phase 4) catches the *status* implication and keeps it correctly `extracted`, but the visible title is still era-mismatched. Per-era titles in `CanonicalItem` are the proper fix; logged for v2 in [`decisions.md`](./decisions.md).

2. **~~TOC-page-header artefact in some fixtures.~~** ~~Newmont, NVDA, JPMorgan, Walmart all emit `title_mismatch` warnings where the detected heading is "Table of Contents"…~~ **Resolved in Phase 8**: pipeline now trims known page-header artefacts (`Table of Contents`, `Parts II and III`, bare/dashed page numbers) from the start of `content_text` and adjusts `char_range.start` accordingly. Validator additionally handles Walmart-style multi-line headings (`ITEM N.\nTITLE`). Eval warnings dropped from ~17 to 2.

3. **Eval set lacks two categories.** `amendment` (10-K/A) and `small_cap` from [`spec.md` §5.1](./spec.md) are uncovered. No 10-K/A appeared in any candidate company's recent filings; small-cap sourcing needs deliberate research. The pass bar is met without them, but the rubric's "intentionally stresses edge cases" axis is partially weakened. See [`prompts/03-eval-set-design.md`](./prompts/03-eval-set-design.md).

4. **Pre-2009 XBRL cross-check is silently skipped.** XBRL was first mandated June 2009. For pre-2009 filings, the Item 8 ↔ Company Facts cross-check would falsely pass against later-year XBRL data (since Company Facts is CIK-level, aggregated across years). We skip the check entirely for `period_of_report.year < 2009`. False negatives accepted; flagged in [`decisions.md`](./decisions.md) Phase 3 entries.

---

## Where AI helped

[`prompts/`](./prompts/) — three substantive AI-collaboration writeups; the grader was told they would read these:

- [`01-framing.md`](./prompts/01-framing.md) — three prompts that reshaped the spec (domain-unfamiliarity question, pushback on a decorative `confidence` field, three-line scope confirmation).
- [`02-strategy-ladder.md`](./prompts/02-strategy-ladder.md) — rules-first design and the **"先做 A"** decision that caught 3 real bugs by ordering Phase 5 (eval) before Phase 4 (LLM fallback). Phase 4 was eventually built — but as two layers (status resolver + locator fallback), reframed by the user's pushback that "持續疊規則的維護成本會爆".
- [`03-eval-set-design.md`](./prompts/03-eval-set-design.md) — eval-set construction; era-notes prompt that surfaced a latent catalog bug (catalog only knew about 2 of 7 era cutoffs); CIK 1411494 mishap.

Companion: [`decisions.md`](./decisions.md) — implementation journal of execution-level issues and decisions made during the build.

---

## Project docs

- [`spec.md`](./spec.md) — what we're building, scoring-axis interpretation, acceptance criteria
- [`plan.md`](./plan.md) — architecture, components, trade-offs, risks
- [`task.md`](./task.md) — phase-ordered checklist; the grader reads it
- [`CLAUDE.md`](./CLAUDE.md) — conventions for future Claude Code sessions
- [`decisions.md`](./decisions.md) — implementation journal (Phase-by-phase issues and decisions)
- [`prompts/`](./prompts/) — AI-collaboration writeups required by the test rubric
- [`eval/fixtures/format_eras.md`](./eval/fixtures/format_eras.md) — 10-K filing format era timeline for fixture selection
