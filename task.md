# task.md — Ordered task list

Time budget: **2.5–4 hours total**. Order matters more than the numbers.

Each phase ends in a **commit** with an intent-revealing message. The grader will read `git log`.

---

## Phase 0 — Repo init  (≈ 5 min)

- [x] `git init` inside `sec-10k-extractor/`
- [x] `.gitignore`: Python, `.env`, `cache/`, `eval/results/*.md` except a sample
- [x] First commit: "scaffold spec/plan/task docs and CLAUDE.md"
- [x] Push this repo to GitHub as a public repo (done in Phase 7: <https://github.com/s8w1e2ep/sec-10k-extractor>)
- [x] `SEC_CONTACT_EMAIL`, `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN`) noted as required Zeabur env vars (done in Phase 7: `zeabur.json` declares them with required-flag and descriptions)

## Phase 1 — AAPL end-to-end, rules-only  (≈ 60 min)

Goal: prove the loop works for one clean modern filing before scaling to messy cases.

- [x] `extractor/canonical_items.py` — 23-item catalog with sort key + period filter + alias list
- [x] `extractor/fetcher.py` — User-Agent + 10 req/s token bucket + on-disk cache + `*.sec.gov` allowlist + 429 backoff
- [x] `extractor/resolver.py` — `(cik, accession) → primary 10-K URL` via Submissions API
- [x] `extractor/format_detect.py` — `html_modern | html_legacy | plain_text`
- [x] `extractor/normalizer.py` — HTML branch only for now, BeautifulSoup walk with offset tracking; emit `NormalizedDoc(text, headings, anchors, format)`
- [x] `extractor/locator.py` — TOC-anchor strategy only
- [x] `extractor/status_detect.py` — rules from plan §2.7
- [x] `extractor/pipeline.py` — wire stages together; return dict matching spec §4.3
- [x] `server/main.py` — `POST /extract`, `GET /extract/{cik}/{accession}`, `GET /healthz`, `GET /`
- [x] `tests/test_canonical_items.py`, `test_status_detect.py`, `test_normalizer_and_locator.py` — 18 unit tests passing
- [x] `tests/smoke_aapl.py` — live integration smoke. AAPL FY2025 10-K (accession `0000320193-25-000079`): 23/23 items, 0 missing, 0 LLM calls, 483ms total / 169ms fetch. Status breakdown matches expectations: 5 IBR (Items 10–14), 1 reserved (Item 6), 4 N/A (1B, 4, 9, 9C), 13 extracted.
- [x] **Commit**: `Phase 1: AAPL end-to-end with TOC-anchor locator`

## Phase 2 — Heading regex + plain text  (≈ 40 min)

- [x] `extractor/locator.py` — `locate_by_heading_regex` (canonical-index monotonic filter to drop TOC echoes / in-body refs) + `combine_strategies` (TOC wins on >200-char disagreement, heading fills gaps, end-recompute after merge)
- [x] `extractor/normalizer.py` — `normalize_plain_text` (SEC pseudo-XML wrapper strip, form-feed → newline, isolated-line heading detection requiring prev-blank)
- [x] `extractor/canonical_items.py` — added `valid_from_year` for 1A (2005), 1B (2005), 7A (1997), 9A (2003), 9B (2004), 15 (2003 renumber), 16 (2016) so `expected_items_for_period` matches real-era filings
- [x] `extractor/resolver.py` — old-style URL `/data/{cik}/{acc-dashed}.txt` support
- [x] `tests/fixtures/aapl_1996_10k.txt` — AAPL FY 1996 10-K (271 KB plain-text submission)
- [x] `tests/test_plain_text.py`, `test_locator_heading.py`, `test_pipeline_plain_text.py` — 19 new unit tests; total 37 passing
- [x] `tests/smoke_aapl_1996.py` — live integration. AAPL FY1996: 14/14 pre-2003 items located, recall=100%, plain_text format, 0 LLM calls, all heading-regex strategy.
- [x] **Commit**: `Phase 2: heading-regex locator + plain-text normalizer`

**Known limitation carried forward**: pre-2003 Item 14 was "Exhibits..." (now Item 15). The locator labels detected items by canonical title (post-2003), so AAPL FY1996 Item 14 shows as "Principal Accountant Fees and Services" in the response title even though content is Exhibits. Item NUMBER is correct; title is era-mismatched. Acceptable for v1; flagged for Phase 8.

## Phase 3 — Validator + warnings  (≈ 30 min)

- [x] `extractor/validator.py` — 6 checks: char_range_invalid (end<start), char_range_overlap, low_coverage (<50%), non_monotonic_order, suspect_brevity (Item 1 <1000 chars while extracted), title_mismatch (canonical-title fuzzy + alias check, threshold=75)
- [x] XBRL Company Facts cross-check — GET `data.sec.gov/api/xbrl/companyfacts/CIK{...}.json`; httpx HTTPStatusError 404 → `xbrl_not_filed` warning; empty us-gaap → `xbrl_no_us_gaap`. Skipped for filings with `period_of_report` < 2009 (predates XBRL mandate); skipped when Item 8 status is non-extracted.
- [x] Wire warnings into pipeline output (validator runs after items_missing check; both surfaced in `warnings`)
- [x] `tests/test_validator.py` — 16 synthetic-input tests covering each warning code + alias-matching + non-extracted skip + pre-2009 XBRL skip; total now 53 unit tests passing
- [x] Smoke verification:
    - AAPL FY 2025 (modern): zero validator warnings (clean filing)
    - AAPL FY 1996 (plain text): one `title_mismatch` on Item 14 (era-rename, the deferred Phase 8 limitation that v3 self-verification catches as designed)
- [x] **Commit**: `Phase 3: validator + self-verification warnings`

## Phase 4 — LLM-backed resolvers  (built post-Phase 8, after user pushback on rules maintenance)

Reframed from the original "LLM fallback locator" plan into **two layers** sharing one per-request call:

- [x] **Layer 1 — status resolver**: when validator emits `title_mismatch` on an `extracted` item, ask Claude Haiku 4.5 to confirm/correct the status. Targets BRK FY 2025 Item 14 (unusual IBR phrasing) and AAPL FY 1996 Item 14 (era-rename). Live eval: BRK status flipped `extracted → incorporated_by_reference`; FY 1996 kept `extracted` correctly.
- [x] **Layer 2 — locator fallback**: when rules left required items unlocated, send first 50 KB + missing list to LLM, recover offsets via `text.find(snippet)`. Currently no eval fixture triggers it (rules at 100% recall); hook is wired and tested.
- [x] `extractor/llm_client.py` — supports both `ANTHROPIC_API_KEY` (X-Api-Key) and `CLAUDE_CODE_OAUTH_TOKEN` (Bearer + `anthropic-beta: oauth-2025-04-20` + Claude Code identifier prefix on the system prompt). Live verified — first try got 401 because Agent advice ("send OAuth via x-api-key") was wrong; fix in commit `16663df`.
- [x] `extractor/prompts/{status_resolver,locator_fallback}.md` — prompt templates with explicit era guidance (pre-2003 Item 14, pre-2011 Item 4, BRK-style IBR).
- [x] Cost tracking: shared per-request budget enforced in pipeline (`MAX_LLM_CALLS_PER_REQUEST`, originally 1 — bumped to 3 in Phase 10); 50KB input cap inside the client; `llm_calls` / `estimated_cost_usd` in `stats`. Live eval cost: **$0.0039 across 10 fixtures** (2 LLM calls).
- [x] `tests/test_llm_resolver.py` — 10 mocked tests; total now 75 unit tests passing.
- [x] **Commit**: `Phase 4: LLM-backed status resolver + locator fallback` (`3fceb4f`); auth fix `16663df`.

## Phase 5 — Eval set + harness  (≈ 40 min)

- [x] `eval/fixtures/filings.jsonl` — 10 entries (modern_clean × 4: AAPL/MSFT/NVDA/TSLA; incorporation_heavy × 3: BRK/WMT/Apollo; bank: JPM; mining: NEM; plain_text: AAPL FY1996). Categories `amendment` and `small_cap` deferred (no readily-available 10-K/A in candidate companies' recent filings; small-cap sourcing needs more research).
- [x] `eval/build_fixtures.py` — Submissions API probe utility that generates filings.jsonl from a candidate-CIK list
- [x] `eval/run_eval.py` — pure stdlib + httpx + rapidfuzz harness; per-fixture metrics (items_recall, status_correctness, strategies, latency, cost); per-category aggregate; pass-bar enforcement; Markdown + JSON output
- [x] Three real bugs surfaced and fixed via the eval cycle (see decisions.md):
    - href regex `\b` failed between digit-and-underscore — replaced with explicit lookahead
    - filers (MSFT/BRK) put title in link text and item number in href — added href-fallback path
    - filers (Apollo/Tesla) split TOC across multiple cells / fragmented anchors — added row-level extraction (`<tr>`/`<li>` whose text matches "Item N. ...")
- [x] Item 16 marked optional (per Form 10-K General Instructions: "may, at their option") — items_missing accounting now excludes voluntary items so BRK/JPM aren't dinged for legitimately omitting it
- [x] First run report at `eval/results/eval-20260430-141249.md`. Headline: **agg_recall=1.000, agg_status_correctness=1.000, p95 modern_clean=1114 ms, total LLM cost $0.00**
- [x] Pass bar **all green**: items_recall ≥ 0.90 ✓, status_correctness ≥ 0.85 ✓, p95 latency modern_clean ≤ 30 s ✓
- [x] **Commit**: `Phase 5: eval set + harness, first run report`

## Phase 6 — README + prompts/  (≈ 25 min)

- [x] `README.md` — full rewrite to reflect Phase 5 state: actual eval results table (1.000 / 1.000 / 1114 ms), three-strategy locator description (TOC anchor with three sub-patterns; heading regex; LLM fallback DEFERRED), 4 honest failure modes (Item 14 era-rename, TOC-page-header artefact, missing categories, pre-2009 XBRL skip), AI collaboration links, project docs index. Live URL still pending Phase 7.
- [x] `prompts/01-framing.md` — three early prompts that reshaped the spec: (1) domain-unfamiliarity forced the central-engineering-challenge framing (SEC mandates structure, not format), (2) "沒有用到就刪除" pushback became no-decorative-fields discipline, (3) three-line scope confirmation
- [x] `prompts/02-strategy-ladder.md` — rules-first design and the **"先做 A"** decision that ordered Phase 5 before Phase 4 and caught 3 real bugs (recall progression 0.687 → 0.861 → 0.991 → 1.000)
- [x] `prompts/03-eval-set-design.md` — eval set construction; era-notes prompt that surfaced the latent `valid_from_year` catalog bug; expected_status_overrides discipline; CIK 1411494 mishap
- [x] `prompts/README.md` — updated index with one-line description per file pointing at the moments that mattered
- [x] **Commit**: `Phase 6: README + prompts/ writeups`

## Phase 7 — Zeabur deploy  (≈ 15 min)

- [x] `Dockerfile` — `python:3.12-slim` + ca-certificates + project; lxml manylinux wheels (no build deps); non-root `app` user; honours `$PORT`; `CACHE_DIR=/app/cache`
- [x] `zeabur.json` — dockerfile build, port 8000, healthcheck `/healthz`, env vars (`SEC_CONTACT_EMAIL` required, `ANTHROPIC_API_KEY` optional)
- [x] `.dockerignore` — excludes everything not needed at runtime (.git, .venv, eval/results, cache, tests, prompts, project docs)
- [x] Pushed to public GitHub: <https://github.com/s8w1e2ep/sec-10k-extractor>
- [x] Zeabur connected; `SEC_CONTACT_EMAIL` set; live URL: <https://sec-10k.zeabur.app>
- [x] `/healthz` returning `{"status":"ok"}`; AAPL FY 2025 smoke against live URL returns 23/23 items via TOC anchor
- [x] Full eval against live URL: 10/10 fixtures, agg_recall=1.000, agg_status=1.000, p95 modern_clean=4821 ms (vs local 1114 ms — cold cache + public-internet SEC hop). Report: `eval/results/eval-20260430-145456.md`
- [x] Live URL added to README header + Eval results table now shows local vs live side by side
- [x] **Commit**: `Phase 7: Zeabur deployment + live eval`

## Phase 8 — Post-deploy polish (catch-all)

This phase captures non-trivial work that surfaced after the original plan shipped.

- [x] **Trim repeating-page-header artefacts from content_text.** User pushed back on the lingering `title_mismatch` warnings: did the "Table of Contents" / "Parts X and Y" prefixes corrupt content extraction? Yes-ish — items were located correctly but content_text contained page-header noise that would have muddied grader-facing output and could miscategorize short reserved/N/A items at the length threshold. Fixed in two layers: (a) `extractor/normalizer.py` adds `is_boilerplate_line()` + `trim_leading_boilerplate()` with conservative patterns; (b) `extractor/pipeline.py` advances `char_range.start` past trimmed boilerplate so the response contract stays consistent; (c) `extractor/validator.py:_extract_section_heading` handles Walmart-style multi-line `ITEM N.\nTITLE` and rejects degenerate `(.+?)` regex backtracking matches. Local eval: warnings ~17 → 2 (remaining 2 are genuine — AAPL FY1996 Item 14 SOX rename, BRK Item 14 unusual IBR opener).
- [x] **Phase 4 LLM resolvers built** (above). The 2 genuine title_mismatch warnings now drive a status correction instead of remaining unresolved.
- [x] **`amendment` no longer needed** — Phase 9A added explicit form gating that rejects 10-K/A with HTTP 400. The "what if a 10-K/A comes in" case is now a contract, not a coverage gap.
- [x] **`small_cap` covered** by Kura Sushi USA in Phase 9C (also tested the shared-anchor edge case it surfaced).

## Phase 9 — Production hardening (post-Phase-8 follow-on)

Triggered by user review of the deployed service, in this order: A → C → D → B, then the three additional hardening tasks (thread offload, defensive input handling, README API spec).

### 9A — Reject non-10-K forms at the gate (≈ 10 min)

- [x] `extractor/pipeline.py:UnsupportedFormError` + `_is_supported_form()` — accepts 10-K family (10-K, 10-KSB, 10-K405, 10-KT) but rejects any `/A` amendment and all non-10-K forms (10-Q, 8-K, 20-F, 40-F, …)
- [x] `server/main.py` returns HTTP 400 with structured body `{error, form, supported_forms}`
- [x] Live verified against Jones Soda 10-K/A
- [x] 22 new tests, total 97
- [x] **Commit**: `Reject non-10-K forms with HTTP 400 + structured detail` (`cd8c5a3`)

### 9C — Industry / era diversity in eval set (≈ 30 min)

User noted the original 10 fixtures skewed tech / mega-cap finance. Added 6 fixtures across small_cap / restaurant / biotech / industrial / luxury_retail / pre-SOX entertainment:

- [x] Kura Sushi USA FY 2025 (CIK 1772177) — small_cap + restaurant. **Surfaced shared-TOC-anchor bug**: Items 11-14 share one anchor while Item 10 has its own, producing inverted `char_range_invalid`.
- [x] Moderna FY 2025 (CIK 1682852) — biotech, large Item 1A
- [x] Caterpillar FY 2025 (CIK 18230) — heavy-machinery / traditional industry
- [x] Chipotle FY 2025 (CIK 1058090) — mid-cap restaurant
- [x] Tiffany & Co. FY 2014/2015 (CIK 98246) — pre-2020 era luxury (Item 6 = Selected Financial Data, not [Reserved]); LVMH alternative since LVMH itself files 20-F
- [x] Walt Disney Co. FY 2002 (CIK 1001039) — pre-SOX entertainment (no Item 1A/1B/9A/9B/15 expected)
- [x] `extractor/locator.py:combine_strategies` — end-computation switched from canonical-adjacency to **offset-adjacency**. Items at the same start (shared anchor) get identical (start, end). Validator's overlap check also walks by offset order with identical-range suppression.
- [x] `eval/probe_new_fixtures.py` — small CLI to find accessions for new (CIK, target_year) pairs, walking both `recent` and older `files` chunks
- [x] **Commit**: `Phase C: 6 new fixtures (industries / older year) + offset-based end fix` (`2fd611b`)

### 9D — Status overrides on every fixture + 3 status_detect rule fixes (≈ 45 min)

Adding `expected_status_overrides` to the 14 fixtures that didn't have them surfaced **3 real status_detect bugs** and **5 of my override-design mistakes** — exactly the eval-as-debugger loop Phase 5 was set up for.

- [x] Standard pattern applied to all post-2020 large-cap fixtures: `{1B: N/A, 4: N/A, 6: reserved, 9: N/A, 9C: N/A, 10-14: IBR}`. Era-aware deviations for Tiffany pre-2020, Disney pre-SOX, Apollo pre-Item-1C.
- [x] Bug fix #1: `_INCORPORATED_RE` was `incorporat\w+\s+(?:here)?in?\s*by\s+reference` — the `in?` group required at least one "i", silently rejecting "incorporated by reference" without "herein". Hit BRK / NVDA / JPM / banks. Replaced with `incorporat\w+(?:\s+\w+)?\s+by\s+reference`.
- [x] Bug fix #2: Items 10-14 now use three IBR signals — direct phrase anywhere; "Refer to Item N" cross-reference (compound IBR, JPM); very short content (< 200 chars) for shared-anchor fragments (Kura Sushi).
- [x] Bug fix #3: `_NOT_APPLICABLE_RE` now matches "none applicable" (Moderna FY 2025 typo); cap raised 300 → 500 to absorb NVDA 9C trailing Part III preamble.
- [x] Override corrections after reading actual filing content: Apollo Items 10-14 (genuinely extracted), BRK Item 4 (real Mine Safety content), Disney FY 2002 Items 4 + 13 (era + extracted), MSFT Item 1B ("no written comments" not "Not applicable").
- [x] `eval/with_server.sh`, `eval/inspect_filing.py`, `eval/show_wrong_statuses.py`, `eval/test_status.py` — reusable debug utilities to replace the inline `python3 -c '...'` snippets that were cluttering iteration cycles.
- [x] Final: 16/16 fixtures `status_correctness=1.000`, `agg_recall=1.000`, 0 LLM calls, p95 modern = 922 ms.
- [x] **Commit**: `Phase D: status overrides on 14 fixtures + 3 status_detect rule fixes` (`fabcdba`)

### 9B — Per-fixture rationale README (≈ 15 min)

- [x] `eval/fixtures/README.md` — one paragraph per fixture grouped by category, naming the era / industry / TOC pattern each one stresses and the bug it surfaced. Closing "How to add a fixture" section threads `probe_new_fixtures.py` → `inspect_filing.py` → `run_eval.py` into a reproducible workflow.
- [x] **Commit**: `Phase B: per-fixture rationale README` (`8075d0d`)

### 9E — Thread-pool offload + 30 MB size cap (≈ 25 min)

User asked: large filings would block the single-worker server while BeautifulSoup parses for 1-5 seconds — does that DoS other users?

- [x] `extractor/pipeline.py:MAX_RAW_HTML_BYTES = 30 MB` + `OversizedFilingError` — rejected before BeautifulSoup is invoked
- [x] `extractor/pipeline.py:_parse_and_normalize` bundled and offloaded via `await asyncio.to_thread(...)` so the event loop stays free during CPU-bound parsing
- [x] `server/main.py` returns HTTP 413 for oversized; the 90s total timeout still applies
- [x] `eval/stress_test.py` — fires 4 heavy fixtures concurrently + pings `/healthz` 50× in parallel; reports healthz percentiles under load
- [x] Local result: `/healthz` p95 = 70 ms while 4 cold filings parse simultaneously (vs blocked >1 s without offload)
- [x] 3 new tests covering size cap + concurrent responsiveness; total 100
- [x] **Commit**: `Hard 30 MB cap + offload BeautifulSoup parse to thread pool` (`015babe`)

### 9F — Defensive input handling (≈ 20 min)

User asked: do we have防呆 for invalid CIK / accession?

- [x] Catalogued existing defenses (Pydantic schema, `_normalize_cik` digit check, `_normalize_accession` format check, form gate, size cap, allowlist) — all already returning structured 400/413/422.
- [x] Closed the gap where SEC 404/5xx bubbled up as bare 500: new `FilingNotFoundError` + `UpstreamError` exceptions in `extractor/pipeline.py`. Caught at three points (Submissions API fetch, accession lookup, document URL fetch).
- [x] `server/main.py` maps to: 404 + `{error, what, where}` for filing not found; 502 + `{error, upstream, upstream_status}` for SEC 5xx / retry exhaustion.
- [x] The `what` field in the 404 body distinguishes "CIK doesn't exist" from "accession not in this CIK's filings" so the caller knows which input to fix.
- [x] 13 new tests covering each error-mapping path including FastAPI TestClient end-to-end; total 113
- [x] Live verified locally: bad CIK → 404, malformed CIK → 400, empty body → 422, AAPL valid → 200
- [x] **Commit**: `Map SEC 404/5xx to proper HTTP codes (no more bare 500s)` (`aada473`)

### 9G — README API spec + structured error catalog (≈ 15 min)

- [x] README's new "API reference" section: 4 endpoints, request schema, success response example, complete error matrix (HTTP code × trigger × body shape × example), hard limits table.
- [x] Side-by-side 404 example showing `what` field disambiguating CIK-not-found vs accession-not-found.
- [x] Old form-gating snippet removed from Quick Start (now lives in error matrix).
- [x] **Commit**: `README: add API reference + structured error response catalog` (`ca32d31`)

## Phase 10 — LLM cap bump + doc reconciliation  (≈ 30 min)

Triggered by user's diagnostic question "tests aren't using LLM — what triggers it?". Answering it surfaced a doc-vs-code drift.

- [x] Audited the LLM trigger conditions; found `CLAUDE.md` and `plan.md` claimed Layer 2 only fires on residual gaps > 5 KB — the real gate at `pipeline.py:198` was just "any required item missing". Drift never caught earlier because no fixture ever tripped either gate.
- [x] Promoted the 1-call cap to a named constant `MAX_LLM_CALLS_PER_REQUEST = 3` in `extractor/llm_client.py`. Both Layer 1 and Layer 2 gates now check `usage.calls < MAX_LLM_CALLS_PER_REQUEST` against the shared `LLMUsage` counter — both layers can fire in the same request.
- [x] Synced docs to current behavior: `CLAUDE.md`, `README.md`, `plan.md`, `spec.md`, `task.md`, plus docstrings in `llm_client.py` / `llm_resolver.py` / `test_llm_resolver.py`. Old `prompts/02-strategy-ladder.md` left intact (historical writeup) with a forward-pointing footnote.
- [x] New test `test_pipeline_allows_layer1_after_layer2_within_budget` — synthetic filing where both gates trip; asserts `usage.calls == 2`.
- [x] Decisions journal entry. Rule of thumb added to `CLAUDE.md`: behavior claims in docs must be reconcilable to a grep.

## Phase 11 — Cost shields (A + B + F)  (≈ 60 min)

Public `/extract` was unauthenticated; with the cap raised to 3 the worst-case per-request cost is ~$0.05, which is a real cost-DoS surface. User picked A + B + F from a six-option menu (rate limit, daily ceiling, API key, result cache, CDN, log forensics) — C/D/E explicitly de-scoped.

- [x] **A — per-IP rate limit** (`server/main.py:rate_limit_middleware`): in-process token bucket keyed by client IP. Default 10 req/min burst 10, env-tunable via `RATE_LIMIT_PER_MIN` / `RATE_LIMIT_BURST`. Reads `X-Forwarded-For` first (Zeabur is behind a reverse proxy). `/healthz`, `/`, `/docs`, `/redoc`, `/openapi.json` exempt. Returns 429 with `Retry-After: 1`.
- [x] **B — daily LLM cost ceiling** (`extractor/llm_client.py`): process-wide accumulator updated atomically inside `LLMUsage.add()`. UTC date rollover resets on read. Default $5/day via `DAILY_LLM_BUDGET_USD`. Pipeline gates degrade to rules-only with a `llm_skipped_daily_budget_exhausted` warning when exhausted — request still returns 200.
- [x] **F — `client_ip` in structured log**: one-line addition to `request_logging_middleware`, same `_client_ip()` helper as the rate limiter so logs and limits agree on identity. Even 429 responses get logged.
- [x] 11 new tests in `tests/test_abuse_prevention.py` covering all three layers including XFF chain stripping, /healthz exemption, date rollover, and pipeline degradation. Total 138/138 passing.
- [x] Synced docs: `CLAUDE.md` (cost-shield convention + don't-pretend-it's-auth note), `README.md` (limits table + abuse prevention section), `plan.md` (R4 mitigation), `zeabur.json` (3 new env vars), `decisions.md` (Phase 11 entry), `prompts/04-cost-shield.md` (the design conversation), `prompts/README.md` (4th file in the table), `prompts/02-strategy-ladder.md` (forward-pointing footnote).
- [x] Honest disclosure baked into all three layers' comments: **cost shield, not security boundary**. State dies on restart, doesn't span workers.
