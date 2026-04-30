# prompts/

Key prompts that shaped this project's design. The grader was told they would read these, so the goal here is signal, not volume — each file documents a moment where AI collaboration changed the direction or substance of the work.

| File | What it documents |
|---|---|
| [`01-framing.md`](./01-framing.md) | Three early prompts that reshaped the spec: (1) user unfamiliarity with the SEC domain forced me to articulate the central engineering challenge (SEC mandates structure, not format) which became the architecture's organizing principle; (2) "沒有用到就刪除" pushback on a `confidence` field, generalised into a no-decorative-fields discipline that influenced later data-model choices; (3) the three-line scope confirmation (time budget OK, defer LLM, no UI) that resolved load-bearing ambiguity. |
| [`02-strategy-ladder.md`](./02-strategy-ladder.md) | The rules-first / LLM-fenced design and the most consequential prompt of the build: **"先做 A"** — two characters that reordered Phase 4 and Phase 5. The eval-first ordering caught 3 real bugs (MSFT/BRK href pattern, Apollo/Tesla anchor fragmentation, Item 16 voluntariness) that the AAPL-only smoke had silently passed. After fixes, rules cover 100% of the eval set with zero LLM calls; Phase 4 indefinitely deferred. |
| [`03-eval-set-design.md`](./03-eval-set-design.md) | The eval-set construction and the era-notes prompt that surfaced a latent catalog bug. User asked for a fixture-selection reference doc; writing it forced me to enumerate every era cutoff (FY 2003 SOX, FY 2011 Dodd-Frank, FY 2019 iXBRL, FY 2021 HFCAA, FY 2023 cybersecurity) which made me notice my catalog only had `valid_from_year` for 2 of 7 era-gated items. Fixing that took AAPL FY 1996 recall from 67% → 100% without touching the locator. Also documents `expected_status_overrides` discipline and the CIK 1411494 (thought it was Stitch Fix, was Apollo) lesson. |

Companion to these: [`../decisions.md`](../decisions.md) is the implementation
journal — issues that surfaced during build and the decisions made in
response. The `prompts/` files cover *design-shaping prompts* (where
collaboration moved the spec); `decisions.md` covers *execution-level
decisions* (why a regex is `(?=[_\-]|\W|$)`, why XBRL skips pre-2009).
