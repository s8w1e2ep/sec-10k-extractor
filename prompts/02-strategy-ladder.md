# 02-strategy-ladder.md — Rules-first design and the "先做 A" decision

This file documents two related design moments: the rules-first/LLM-fenced
strategy ladder (mostly proposed by me and validated by the user), and the
single most consequential prompt of the build — the user's two-character
choice that reordered Phase 4 and Phase 5.

## The strategy ladder, as designed

`spec.md` §1 framed it sharply:

> If a 1996 plain-text filing and a 2024 inline-XBRL filing both cost the
> same number of LLM tokens to parse, we have built the wrong system.

This was the cost-discipline rule. The system was structured so that:

- **TOC anchor** strategy is tried first. Robust on modern filings.
- **Heading regex** strategy runs *always* as a sanity check, fills gaps
  TOC missed, and votes against TOC on disagreement (TOC wins on conflict).
- **LLM fallback** is fenced: max 1 call per request, 50 KB input, only
  fires when residual gaps > 5 KB exist after both rule strategies.

> **Footnote, Phase 10**: this paragraph reflects the *original design
> intent*. Two of those three numbers were never actually implemented:
> the "5 KB residual gap" filter never made it into code (the real gate
> is just "any required item missing"), and the per-request cap was
> raised from 1 → 3 calls. See [`04-cost-shield.md`](./04-cost-shield.md)
> for the conversation that surfaced and fixed both.

The cost-discipline numbers (1 call / 50 KB / 5 KB threshold) were spec'd
upfront in `plan.md` §2.6. The reasoning: if rules can't crack it, hand
the LLM only the *residual* range — not the whole document — so the cost
is bounded by gap size, not document size.

The user reviewed `plan.md` and accepted these without modification. Mostly
my proposal.

## The decision that changed everything: "先做 A"

After Phase 3 shipped, I summarized the next-step options:

> **Phase 4**: LLM fallback locator (殘缺 > 5KB 才觸發、單次最多 50KB、
> cost 紀錄)
>
> **Phase 5**: Eval set + harness (≥ 12 fixture、跑 metrics、寫 markdown 報告)
>
> 按你的話「LLM fallback 後續再決定」，可以選擇:
> - A. **直接跳到 Phase 5** (先建 eval 集 + harness，跑現有規則策略看 baseline；
>   如果 recall 已夠就不做 LLM)
> - B. **先做 Phase 4** (按原計畫順序)
>
> 我建議 A——先量化現有規則的覆蓋率，再判斷 LLM 是否真的需要。要哪個？

The user replied: **"先做 A"**.

Two characters. Reordered the build plan. And it caught three real bugs
that the AAPL-only smoke had silently passed for three phases.

### What the eval surfaced that smokes hadn't

First eval run: **agg_recall = 0.687** against the 0.90 pass-bar.

Three filings returned **zero items**:

1. **MSFT** (FY 2025) — 8 MB filing, 23 expected items, 0 found.
2. **Berkshire Hathaway** (FY 2025) — 10 MB filing, 0 found.
3. **Apollo Asset Management** (FY 2022) — 5 MB filing, 0 found.

Until that moment, the smoke test on AAPL FY 2025 had returned 23/23 items
with zero LLM calls. I would have shipped Phase 4 with the assumption that
"rules cover the head case; LLM is for the tail."

What the eval revealed:

- **MSFT/BRK** put the Item title in the link text and the item NUMBER in
  the href (`<a href="#item_1_business">Business</a>`). My TOC regex required
  the link text itself to start with "Item N." — none of these matched.
  The TOC was *more* structured than AAPL's, just structured differently.
- **Apollo/Tesla** split TOC entries across multiple `<a>` tags — Apollo
  has GUID anchors with item numbers in adjacent `<td>` cells, Tesla
  fragmented "Item 1C" across two anchors ("Item 1" and "Cybersecurity")
  pointing to the same offset.

These were not edge cases. These were three of the largest companies in
the world. The "head case" turned out to be *Apple's specific TOC
convention*, not a representative pattern.

### Recall progression after each fix

| Iteration | agg_recall | Fix |
|---|---|---|
| 1 | 0.687 | (initial) |
| 2 | 0.861 | href regex `\b` → lookahead (handles `#item5_market`) |
| 3 | 0.991 | row-level `<tr>` extraction (handles fragmented anchors) |
| 4 | **1.000** | Item 16 marked optional (per Form 10-K instructions) |

After iteration 4, **rules covered 100% of the eval set with zero LLM
calls** — the LLM fallback's residual-gap trigger condition (>5 KB gap
after rules) is satisfied on no fixture. Phase 4 was indefinitely deferred.

## What got cut, and what the deferral means

If "先做 B" had won, Phase 4 would have built an LLM fallback that:

- never gets invoked on the eval set,
- requires `ANTHROPIC_API_KEY` env wiring,
- adds an `extractor/prompts/locator_fallback.md` template,
- needs cost-tracking plumbing in `stats`,
- adds a `tests/test_locator_llm.py` with mock fixtures,
- ships untested in production unless we go find a filing rules can't crack.

All of that is still on `task.md` Phase 4 as a deferred backlog item. The
trigger to build it is now well-defined: a future fixture has a residual
> 5 KB gap that neither TOC anchor nor heading regex can locate. Until
that fixture exists, building Phase 4 is speculation.

## Generalizing

The pattern: **build the cheaper layer, measure with a representative
eval, then decide on the expensive layer**. The user's "先做 A" enacted
this pattern. I had proposed it, but the proposal was conditional ("我建議
A"); the user took the action. Without the user's pick, I might have
deferred to "the spec says we'll have LLM fallback, so build it" and
shipped a feature that solved a problem we don't have.

Two-character prompts can carry more design weight than long ones.
