# 04-cost-shield.md — LLM cap bump + abuse prevention

This file documents three design-shaping prompts from a single
conversation that landed Phases 10 and 11. The thread started with a
diagnostic question and ended with a deployed cost shield.

## Move 1 — "目前測資似乎都沒有用到 LLM 做處理？想了解什麼情況下會觸發"

A diagnostic question. The user noticed that the eval set was running
end-to-end without any LLM calls (`stats.llm_calls == 0` on every
fixture) and wanted to know the trigger conditions.

Answering it forced me to read the actual gate code rather than recall
the spec. The gate at `pipeline.py:198` was just:

```python
if missing_now and is_configured():
```

Whereas `CLAUDE.md`, `plan.md`, and `prompts/02-strategy-ladder.md` all
claimed the LLM "only fires when residual gaps > 5 KB exist". That gap
filter was design intent that **never got implemented**. The docs had
frozen the intent, not the reality.

This is the kind of drift that's easy to introduce and hard to notice:
the original `plan.md` design said "5 KB residual gap"; nobody ever
wrote the check; the code shipped; the doc kept claiming the constraint
was active. A reader who trusted the doc would assume cost was bounded
by gap size when in fact the trigger was just "any required item
missing".

**Generalised rule** I added to `CLAUDE.md`: anything in `CLAUDE.md`
describing pipeline behavior gets verified against the code on every
revisit. Documentation that *describes* behavior must be reconcilable
to a grep — if it isn't, it's a story, not a spec.

## Move 2 — "修改描述跟現狀對齊，另外，我希望 LLM call 上限調到 3 次"

A two-clause directive. The first half was a chore (sync the docs);
the second half was a deliberate cap bump.

The pre-existing cap was 1 call per request, enforced implicitly: Layer 2
fired first if missing items existed; Layer 1 only fired if
`usage.calls == 0` afterwards. The two layers were mutually exclusive.

The user's instinct here was right. The "1-call cap" had been justified
in `plan.md` as cost discipline, but in practice it forced an unnecessary
trade: a filing that *both* missed a required item *and* had a
title_mismatch on a different item could only get one of the two
problems addressed. Worst-case cost at $0.05/req was already trivial
relative to the SEC fetch cost; the binding constraint was never the
per-request budget anyway.

I promoted the cap to a named constant
`MAX_LLM_CALLS_PER_REQUEST = 3` in `llm_client.py` and changed both
gates to check `usage.calls < MAX_LLM_CALLS_PER_REQUEST` against the
shared `LLMUsage` counter. Both layers can now fire in the same
request. Realistic ceiling today is 2 (each layer makes one call); the
headroom to 3 leaves a slot for a future Layer 2 retry / chunked pass
without re-architecting.

The bump prompted a more interesting question — what was the *real*
cost ceiling now? Per-request worst case at Haiku 4.5 prices is
~3 × $0.015 ≈ $0.05. Multiplied by an unauthenticated public endpoint,
that's a real cost-DoS surface I had to flag.

## Move 3 — "我覺得做 A + B + F 就好，做快一點，不要浪費我時間"

The user's pick from a tradeoff menu I'd presented. The menu was:

| | What | Effort |
|---|---|---|
| A | Per-IP rate limit (in-process token bucket) | ~30 min |
| B | Daily LLM cost ceiling (process-wide accumulator) | ~15 min |
| C | Optional shared-secret API key | ~10 min |
| D | Result cache (memoize by URL) | ~20 min |
| E | CDN-level rate limit (Cloudflare) | ops change |
| F | client_ip in structured log | ~3 min |

I had recommended A + B + F: A blocks 90% of automation, B is the cost
floor when A leaks, F lets you investigate when both fail. The user
agreed and explicitly de-scoped C/D/E — C and D add complexity for
marginal gain over A+B; E is a production-time concern outside the
coding-test scope.

The "做快一點" framing mattered. It told me: the *kind* of solution
the user wanted was a small, focused, in-process bundle — not the
"properly engineered" version with Redis, Cloudflare, and an admin
panel. A staff engineer's instinct to over-build was actively unwanted
here. Implementation took ~50 minutes including 11 new tests; total
diff was three files.

### What got built

- **A** — `_SyncTokenBucket` keyed by client IP, defaults 10 req/min
  burst 10, env-tunable. Reads `X-Forwarded-For` first because Zeabur
  is behind a reverse proxy. Whitelists `/healthz`, `/`, `/docs`,
  `/redoc`, `/openapi.json` so monitors don't flap.
- **B** — `daily_budget_remaining()` reads
  `DAILY_LLM_BUDGET_USD` (default $5), accumulator updated inside
  `LLMUsage.add()` so every call lands regardless of layer. Pipeline
  gates check the remaining budget; on exhaustion the request still
  succeeds via rules-only with a `llm_skipped_daily_budget_exhausted`
  warning.
- **F** — `client_ip` field added to the existing structured
  `http.request` log line, sourced from the same `_client_ip()` helper
  the rate limiter uses (so logs and limits agree on identity).

### What got explicitly *not* built

The user declined C (API key gate), D (result cache), and E (CDN). My
recommendation already favored leaving these out, and the "做快一點"
framing made the de-scoping fast and final. No half-built scaffolding
left in the tree.

## Generalising

Three patterns worth noting from this thread:

1. **Diagnostic questions surface invisible drift.** The user's
   "why aren't tests using the LLM?" was a curiosity question, not a
   bug report. Answering it required reading the gate code, which
   exposed a doc claim that hadn't been true for some time. The bug
   wasn't a behavior bug — it was a *believability bug* in the
   documentation.

2. **De-scoping is a feature.** The C / D / E options were genuinely
   useful in some imaginable future. The user's "做快一點" was a clean
   refusal to optimize for that future. The cost shield got built at
   the right altitude for *this* deployment, not a hypothetical one.

3. **In-process state is honest about what it can promise.** The rate
   limiter and daily counter both die on container restart and don't
   span workers. I documented this explicitly in the code comments and
   in `decisions.md` rather than dressing it up as a security feature.
   "Cost shield, not security boundary" is the right frame; pretending
   otherwise would have been worse than not having it.
