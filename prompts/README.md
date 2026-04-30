# prompts/

Key prompts that shaped this project's design. The grader was told they would read these, so the goal here is signal, not volume — each file documents a moment where AI collaboration changed the direction or substance of the work.

The buckets planned for v1:

| File | What it documents |
|---|---|
| `01-framing.md` | Initial framing decisions: status-as-textual (not structural), char_range against normalized text (not raw HTML), `items_missing` as a counter (not a `status` value). |
| `02-strategy-ladder.md` | Why rules-first with LLM as a fenced last resort. The cap (1 call / 50 KB / residual > 5 KB) and the cost numbers behind it. What got cut from the LLM path and why. |
| `03-eval-set-design.md` | Category-coverage rationale for the ≥ 12 fixtures. `expected_status_overrides` mechanism. Why hand-curated beats auto-sampling 100 random filings under this rubric. |

Files are populated as the corresponding phases land in `task.md`.
