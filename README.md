# sec-10k-extractor

> Submission for **Task 3** of an AI Coding Test: SEC 10-K item-level structured extraction. Hybrid rules-first / LLM-fallback pipeline that takes a filing (by `CIK + accession` or `file_url`) and emits structured JSON across the 22 canonical 10-K items.

**Status**: scaffold. See [`task.md`](./task.md) for the ordered build plan.

**Live URL**: _(to be added at Phase 7)_

---

## Quick start

_Requires Python 3.12, `SEC_CONTACT_EMAIL` env var, optionally `ANTHROPIC_API_KEY`._

```bash
pip install -r requirements.txt
SEC_CONTACT_EMAIL='you@example.com' uvicorn server.main:app --reload --port 8000

# extract by CIK + accession
curl -X POST http://localhost:8000/extract \
  -H 'content-type: application/json' \
  -d '{"cik":"320193","accession_number":"0000320193-24-000123"}'

# extract by direct file URL
curl -X POST http://localhost:8000/extract \
  -H 'content-type: application/json' \
  -d '{"file_url":"https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm"}'
```

Response shape: see [`spec.md` §4.3](./spec.md).

---

## What this is

10-K filings have a fixed item catalog (22 items across Parts I–IV, post-2023) but their rendering varies enormously: modern inline-XBRL HTML, legacy HTML, pre-2002 plain-text filings, 10-K/A amendments that re-state only some items. The grader calls `/extract` with their own selected filings; we return structured JSON they can verify.

**Per-item record**: `part`, `item_number`, `item_title`, `content_text`, `char_range`, `status`, `resolved_by`.

**Status** is a textual property the filer wrote inside an otherwise-present item — `extracted` / `incorporated_by_reference` / `not_applicable` / `reserved` — not a structural one. Status detection runs after locating, on the located content.

---

## Architecture

See [`plan.md` §1](./plan.md) for the full diagram. Pipeline:

1. **Resolver** — `(cik, accession) | file_url` → primary 10-K URL (via Submissions API).
2. **Fetcher** — User-Agent + 10 req/s token bucket + on-disk cache + `*.sec.gov` allowlist.
3. **Format detect** — `html_modern | html_legacy | plain_text`.
4. **Normalizer** — raw → normalized text + offset map. Offsets in `char_range` are into this text.
5. **Locator** — `toc_anchor` → `heading_regex` → `llm_fallback` (last resort, capped).
6. **Status detect** — per-item rules-only classifier.
7. **Validator** — monotonicity, char-range continuity, canonical title fuzzy match, XBRL Company Facts cross-check; emits `warnings`.

**Cost discipline**: a clean modern filing should make zero LLM calls. The LLM fallback is fenced — max 1 call/request, max 50 KB input, fires only on residual gaps > 5 KB.

---

## Self-verification (no public ground truth)

We don't have ground truth, so we lean on independent signals (full list in [`spec.md` §4.6](./spec.md)):

- Cross-strategy agreement (TOC anchor vs heading regex)
- char_range non-overlap and ≥ 60% coverage
- Item-number monotonicity within Parts
- Canonical title fuzzy match
- XBRL Company Facts: if Item 8 is `extracted`, Company Facts API must return ≥ 1 fact

Disagreements surface as `warnings[]` in the response. They don't fail the request — they signal where to look.

---

## Eval set

Hand-curated fixtures at [`eval/fixtures/filings.jsonl`](./eval/fixtures/filings.jsonl). Categories ([spec §5.1](./spec.md)):

- `modern_clean` — AAPL, MSFT recent
- `incorporation_heavy` — Items 10–14 incorporated by reference
- `plain_text` — pre-2002 `.txt` filing
- `amendment` — 10-K/A
- `small_cap` — micro-cap stress test
- `new_items_2023` — covers Item 1C (cybersecurity)
- `mining` — Item 4 actually populated
- `bank` — Industry Guide 3 disclosures

Run: `python eval/run_eval.py http://localhost:8000` → `eval/results/eval-<timestamp>.md`.

Pass bar:
- `items_recall` ≥ 0.90
- `status_correctness` ≥ 0.85 on fixtures with expected overrides
- p95 latency ≤ 30 s on `modern_clean`

---

## Honest failure modes

_(populated through Phase 5–6 as we hit them)_

- **Pre-2002 plain-text filings**: 80% recall is the v1 bar; varied formatting defeats simple heuristics.
- **10-K/A amendments**: items missing by design (filer only restated some). Validator warns; doesn't fail.
- **`incorporated_by_reference` false negatives**: filer phrasing varies; we backstop with proxy / DEF 14A pattern matching but ~5% of cases slip.

---

## Where AI helped

See [`prompts/`](./prompts/) for prompt records that shaped design decisions. Highlights _(populated through Phase 6)_:

- `01-framing.md` — status-as-textual vs status-as-structural decision
- `02-strategy-ladder.md` — when the LLM fence trips, what got cut
- `03-eval-set-design.md` — category coverage rationale; why hand-curated beats auto-sampled

---

## Project docs

- [`spec.md`](./spec.md) — what we're building, scoring-axis interpretation, acceptance criteria
- [`plan.md`](./plan.md) — architecture, components, trade-offs, risks
- [`task.md`](./task.md) — ordered phases; grader reads it
- [`CLAUDE.md`](./CLAUDE.md) — conventions for future Claude Code sessions
