# CLAUDE.md — sec-10k-extractor

This file gives future Claude Code sessions the context to keep working on this project without re-deriving conventions from scratch.

## What this project is

A submission for **Task 3** of an AI Coding Test: a pipeline that takes a 10-K filing (by `CIK + accession_number` or `file_url`), pulls it from SEC EDGAR, and emits structured JSON — one record per Item across Parts I–IV — including a `status` that distinguishes real content from `incorporated_by_reference` / `not_applicable` / `reserved`. Deployed on **Zeabur** as a public API.

**Key framing — do not lose this:**

- 10-Ks have a fixed item catalog (23 items post-2023) but enormously varied rendering: modern inline-XBRL HTML, legacy HTML, pre-2002 plain text, 10-K/A amendments. The system is designed around handling the **long tail honestly**, not just the head.
- `status` is a **textual property** the filer chose to write inside an otherwise-present item, not a structural one. Status detection runs **after** locating, on the located content. An incorporated-by-reference Item 10 is still a *located* Item 10.
- `char_range` is offsets into our **normalized plain text**, NOT raw HTML. The normalizer is the single source of truth for offsets; any change to it invalidates char-range golden tests on purpose.
- This is a **cost-disciplined system**. Rules first. The LLM exists for the long tail, capped at 1 call per request. A clean modern filing should cost $0 in LLM.
- "Verify yourself without public ground truth" is the central engineering challenge. We use cross-strategy agreement, char-range continuity, item-number monotonicity, and XBRL Company Facts as independent signals. Disagreements surface as `warnings`, not silent corrections.

## Layout

```
sec-10k-extractor/
├── extractor/                # core pipeline (no FastAPI imports here)
│   ├── canonical_items.py    # 23-item catalog + sort/match helpers
│   ├── fetcher.py            # SEC HTTP client (UA, rate limit, cache, allowlist)
│   ├── resolver.py           # (cik, accession) → primary doc URL
│   ├── format_detect.py      # html_modern | html_legacy | plain_text
│   ├── normalizer.py         # raw → NormalizedDoc(text, headings, anchors)
│   ├── locator.py            # toc_anchor → heading_regex → llm_fallback
│   ├── status_detect.py      # per-item rules-only classifier
│   ├── validator.py          # warnings (monotonicity, ranges, XBRL cross-check)
│   ├── pipeline.py           # orchestration; returns response dict
│   └── prompts/              # LLM prompt templates (not Claude Skills)
├── server/                   # thin FastAPI layer
│   ├── main.py               # /extract, /extract/{cik}/{accession}, /healthz, /, /eval/last
│   └── static/               # index.html stub form (grader convenience)
├── tests/                    # pytest unit + golden tests
│   └── fixtures/             # cached EDGAR responses; checked in; small only
├── eval/
│   ├── fixtures/filings.jsonl   # hand-curated ≥ 12-filing eval set
│   ├── run_eval.py           # pure stdlib + httpx harness
│   └── results/              # eval-<timestamp>.md + _latest.json
├── prompts/                  # AI-collaboration writeups (NOT LLM prompt templates)
├── cache/                    # on-disk fetcher cache (gitignored)
├── Dockerfile
├── zeabur.json
├── pyproject.toml / requirements.txt
├── README.md
├── spec.md                   # what we're building, scoring-axis interpretation
├── plan.md                   # architecture, components, trade-offs, risks
├── task.md                   # ordered phases; grader reads it
└── CLAUDE.md                 # this file
```

Note the two `prompts/` directories are different on purpose:
- `extractor/prompts/` — LLM templates the runtime invokes (locator fallback).
- `prompts/` (project root) — prose writeups of the **human** prompts that shaped design decisions; required by the test rubric.

## Conventions

- **Rules-first locator**, LLM as fenced last resort. Order: TOC anchor → heading regex → LLM fallback. The LLM is capped at 1 call per request, 50 KB input, only fires when residual gaps > 5 KB exist.
- **Status detector is rules-only.** No LLM. The patterns (`incorporated by reference`, `not applicable`, `reserved`) are tight.
- **Single source of truth for offsets** is `extractor/normalizer.py`. Char-range tests assert exact offsets on a checked-in golden filing. Changing the normalizer changes offsets — that's a deliberate snapshot bump, not a free move.
- **`items_missing` is a counter on `stats`, not a `status` value**. `status` describes what the filer wrote; `items_missing` describes what we couldn't find. Don't conflate them — that hides our errors.
- **Strategy disagreement surfaces as `warning`, not a silent merge.** TOC wins on disagreement (it's what the filer themselves labelled).
- **Outbound allowlist** at the fetcher level: only `*.sec.gov`. Even if anything else slips into a URL, the fetcher rejects it.
- **SEC etiquette**: User-Agent must contain a contact email (`SEC_CONTACT_EMAIL` env). 10 req/sec is the hard ceiling; we use a single-worker token bucket. Without UA, SEC returns 403.
- **Cache by URL hash** at `cache/<sha256>.bin`. Survives reruns, drops 99% of eval cost on the second run. Gitignored.
- **No database.** File cache only. v1 doesn't need state beyond the filing cache.
- **Self-contained `extractor/` module.** No FastAPI imports inside `extractor/`. The pipeline is callable from a CLI or a notebook the same way.
- **Commit hygiene.** Small, intent-revealing commits matching `task.md` phases. Don't squash; don't force-push. The grader reads commit history.
- **Prompt logging discipline.** When a prompt materially shaped a design decision, save it under `prompts/<NN>-<topic>.md` with a one-line header. Three substantive files beats ten low-signal ones.

## Things NOT to do

- Don't move `char_range` to raw HTML offsets. They're brittle across reformats and the grader can't verify them without parsing HTML.
- Don't add LLM calls to the status detector. The patterns are textual, tight, and rules cover them at $0.
- Don't treat "items_missing" as a `status` value. It's a counter; conflating them hides our errors.
- Don't auto-sample 100 random filings into the eval set. Twelve deliberately-stressed fixtures beat a hundred random clean ones.
- Don't lift the 1-call/request LLM cap to "improve recall on hard cases" without a measured cost/benefit number. The cap is the cost discipline.
- Don't multi-worker the server. The rate limiter is in-process; multi-worker silently bursts past 10 req/s. Document this; don't fix it without sharing the bucket.
- Don't auto-merge strategy disagreements. They're a signal of a bad filing or a bad rule, not noise.
- Don't put 10 MB filings into `tests/fixtures/`. Keep golden fixtures small; cache the rest under `cache/` (gitignored).
- Don't write to `.github/workflows/` from this repo's CI — there's no overlap with Task 1; this repo only ships its own service.

## Reference

- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- Submissions API shape: `https://data.sec.gov/submissions/CIK{0:010d}.json`
- Company Facts API: `https://data.sec.gov/api/xbrl/companyfacts/CIK{0:010d}.json`
- Full-text search (optional convenience): `https://efts.sec.gov/LATEST/search-index?q={q}&forms=10-K`
- Item catalog (23 items as of FY 2023+; 1C added 2023, 9C added 2021): `extractor/canonical_items.py`
- Filing format era timeline (for picking eval fixtures): `eval/fixtures/format_eras.md`
- 10-K item structure background: SEC Form 10-K General Instructions
