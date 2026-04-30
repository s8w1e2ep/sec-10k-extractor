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

- [ ] `extractor/canonical_items.py` — 22-item catalog with sort key + period filter + alias list
- [ ] `extractor/fetcher.py` — User-Agent + 10 req/s token bucket + on-disk cache + `*.sec.gov` allowlist + 429 backoff
- [ ] `extractor/resolver.py` — `(cik, accession) → primary 10-K URL` via Submissions API
- [ ] `extractor/format_detect.py` — `html_modern | html_legacy | plain_text`
- [ ] `extractor/normalizer.py` — HTML branch only for now, BeautifulSoup walk with offset tracking; emit `NormalizedDoc(text, headings, anchors, format)`
- [ ] `extractor/locator.py` — TOC-anchor strategy only
- [ ] `extractor/status_detect.py` — rules from plan §2.7
- [ ] `extractor/pipeline.py` — wire stages together; return dict matching spec §4.3
- [ ] `server/main.py` — `POST /extract`, `GET /healthz`
- [ ] `tests/test_pipeline_aapl.py` — golden test against AAPL most recent 10-K (cached fixture); items_missing == 0; Item 6 == reserved
- [ ] **Commit**: `Phase 1: AAPL end-to-end with TOC-anchor locator`

## Phase 2 — Heading regex + plain text  (≈ 40 min)

- [ ] `extractor/locator.py` — heading-regex strategy; pattern + monotonic ordering filter; runs on every filing as a sanity check
- [ ] `extractor/normalizer.py` — plain-text branch; form-feed handling; ALL-CAPS heading detection
- [ ] `tests/fixtures/` — pre-2002 plain-text filing committed (small, representative)
- [ ] `tests/test_pipeline_plain_text.py` — items_recall ≥ 0.8 against the fixture
- [ ] **Commit**: `Phase 2: heading-regex locator + plain-text normalizer`

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
