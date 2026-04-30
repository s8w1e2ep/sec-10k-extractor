# task.md — Ordered task list

Time budget: **2.5–4 hours total**. Order matters more than the numbers.

Each phase ends in a **commit** with an intent-revealing message. The grader will read `git log`.

---

## Phase 0 — Repo init  (≈ 5 min)

- [x] `git init` inside `sec-10k-extractor/`
- [x] `.gitignore`: Python, `.env`, `cache/`, `eval/results/*.md` except a sample
- [x] First commit: "scaffold spec/plan/task docs and CLAUDE.md"
- [ ] Push this repo to GitHub as a public repo
- [ ] `SEC_CONTACT_EMAIL`, `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN`) noted as required Zeabur env vars

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

- [ ] `extractor/validator.py` — monotonicity, non-overlap, coverage, fuzzy title match
- [ ] XBRL Company Facts cross-check (GET, 404-tolerant, warn on Item 8 mismatch)
- [ ] Wire warnings into pipeline output
- [ ] `tests/test_validator.py` — synthetic ItemSpan inputs covering each warning
- [ ] **Commit**: `Phase 3: validator + self-verification warnings`

## Phase 4 — LLM fallback locator  (≈ 30 min)

- [ ] `extractor/locator.py` — `llm_fallback` strategy; triggered only on residual gaps > 5 KB; max 1 call/request; max 50 KB input
- [ ] Prompt template at `extractor/prompts/locator_fallback.md`
- [ ] Cost tracking: input/output tokens, estimated USD; surfaced in `stats`
- [ ] `tests/test_locator_llm.py` — mocked Anthropic client; verify cap is enforced
- [ ] **Commit**: `Phase 4: LLM fallback locator with hard cost caps`

## Phase 5 — Eval set + harness  (≈ 40 min)

- [ ] `eval/fixtures/filings.jsonl` — ≥ 12 entries spanning all categories from spec §5.1
- [ ] `eval/run_eval.py` — pure stdlib + httpx; per-fixture metrics; per-category breakdown; markdown + JSON output
- [ ] First run against local URL; capture report at `eval/results/eval-<timestamp>.md`
- [ ] Pass bar verification: items_recall ≥ 0.90, status_correctness ≥ 0.85, p95 latency ≤ 30s on modern_clean
- [ ] **Commit**: `Phase 5: eval set + harness, first run report`

## Phase 6 — README + prompts/  (≈ 25 min)

- [ ] `README.md` — architecture, strategy ladder, scoring-axis verification map, ≥ 3 honest failure modes, cost discipline, AI collaboration links, live URL
- [ ] `prompts/01-framing.md` — initial framing decisions (status as textual, not structural; char_range against normalized text)
- [ ] `prompts/02-strategy-ladder.md` — why rules-first, when the LLM fence triggered, what got cut
- [ ] `prompts/03-eval-set-design.md` — category coverage rationale, expected_status_overrides, why no auto-sampling
- [ ] **Commit**: `Phase 6: README + prompts/ writeups`

## Phase 7 — Zeabur deploy  (≈ 15 min)

- [ ] `Dockerfile` — slim Python + lxml + dependencies; volume mount for `cache/`
- [ ] `zeabur.json` — health check, env vars
- [ ] Push to GitHub; connect Zeabur; set env vars
- [ ] Live URL allocated; `/healthz` returning 200; outbound to `data.sec.gov` succeeds
- [ ] Run `eval/run_eval.py` against live URL; commit final report
- [ ] Live URL added to README
- [ ] **Commit**: `Phase 7: Zeabur deployment + live eval`

## Phase 8 — Post-deploy polish (catch-all)

This phase captures non-trivial work that surfaced after the original plan shipped.

- [ ] (placeholder; populate as discoveries land)
