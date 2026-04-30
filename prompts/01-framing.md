# 01-framing.md — Initial framing prompts that shaped the spec

The grader will read this file expecting to see *moments where AI
collaboration moved the design*, not transcripts of code generation. Three
real prompts from the build conversation reshaped the spec materially.

## "我對 SEC 10-K 不是很了解，請盡可能簡單地解釋"

The user opened by saying they were unfamiliar with the SEC filings
domain. This was load-bearing for two reasons.

First, it forced me to *write the spec for someone learning the domain*,
not for myself. Phrasing like "Item 6 is Reserved as of 2021 — used to be
Selected Financial Data" is what showed up in `spec.md` because I was
actively explaining it to someone for the first time. Without that, I'd
have left it as "Item 6 = `[Reserved]`" with no context, which would have
made the catalog choices look arbitrary.

Second, the user's question "為什麼結構是固定的?" triggered me to articulate
the **central engineering challenge** in this task: the SEC mandates a
structure but not a format. That sentence — "規定的是 Item 編號和標題,
但檔案格式從 1990 年代純文字到 2024 年內嵌 XBRL 都有" — became the framing
for the rules-first / multi-strategy locator. If the format were uniform,
one strategy would do. Because rendering varies but structure doesn't,
the locator has to be format-aware while the catalog is format-agnostic.

This insight shaped the whole pipeline split: format-aware **normalizer**
+ format-aware **locator strategies** + format-agnostic **status
classifier** + format-agnostic **canonical catalog**. Each layer has its
own concern. That's now codified in `extractor/`.

## "沒有用到就刪除，我不想有多餘且無用的敘述"

This came up when the user reviewed the spec and noticed I had a
`confidence: 0.97` field in the example response without ever defining how
it would be computed. They didn't ask me to define it — they asked me to
delete it.

Stated as a single sentence it sounds trivial, but the principle was
load-bearing for the rest of the build:

> Don't add fields, abstractions, or descriptions to the spec without a
> defined consumer for them.

I had been adding "looks helpful" surface area — a confidence number, a
fuzzy-match threshold abstraction with no caller, a `Warning_` class in
`types.py` that ended up never imported. The user's correction
generalized: every time I considered adding a field after this, I asked
"what reads it?" If the answer was "no one yet but it'd be nice", I
dropped it.

Concrete downstream: `ItemSpan` and `ExtractedItem` ended up with the
minimum fields that the response contract actually exposes. The Phase 3
validator extracts the section heading from `content_text` directly
instead of carrying a `detected_title` field — partly for the data-model
reasons in `decisions.md`, but the *willingness* to do it that way came
from the "no decorative fields" principle.

## "1. 時間上可行  2. LLM fallback 後續再決定  3. 題目要求是 API，就不用前端 UI"

This was a three-line scope confirmation in response to my "before Phase 1
push back on these three points" summary. It looks unremarkable but
collapsed three architectural questions:

1. **Time budget**: 2.5–4 h vs the 90–120 min Task 1 budget. The user
   accepting the larger budget gave me permission to put the eval set,
   validator, and pre-2002 plain-text path on the critical path rather
   than cutting them. The eval set is the part that ended up surfacing
   the real bugs (see `02-strategy-ladder.md`); without the budget for
   it, those bugs would have shipped.
2. **LLM fallback**: "後續再決定" — defer, don't predicate. This let me
   build a rules-only pipeline first, validate it on a real eval set, and
   *measure* whether LLM fallback was needed. After Phase 5, agg recall
   was 1.000 with zero LLM calls; Phase 4 was indefinitely deferred. If
   the user had insisted "include LLM fallback in v1", I'd have built a
   feature that wouldn't have fired on any fixture.
3. **No frontend**: the test prompt explicitly says "API". Confirmed
   removal of UI ambition. The `GET /` HTML stub in `server/main.py` is
   the entire frontend — five lines of vanilla JS — and that's enough.

Three short answers, three meaningful scope decisions. The willingness to
say "no" or "defer" on each saved a meaningful amount of work that would
have shown up later as friction.

---

These three prompts are a useful contrast. (1) was an information request
that happened to reshape the spec. (2) was a single-sentence pushback
that became a discipline rule. (3) was scope confirmation that resolved
ambiguity I'd flagged in advance. None of them are "user wrote a clever
prompt"; all of them are "user looked at what I'd written and pushed
back, asked for context, or chose between options I'd surfaced."

The lesson I'd carry into other AI-collaboration projects: most useful
prompts aren't novel ideas from the user — they're forcing-functions for
the AI to articulate trade-offs the user can then judge.
