"""Read eval/results/_latest.json and print per-fixture status_wrong details.

Use after `python eval/run_eval.py` to see exactly which (fixture, item)
disagree between expected_status_overrides and pipeline output.

Usage:
    .venv/bin/python eval/show_wrong_statuses.py
    .venv/bin/python eval/show_wrong_statuses.py --only-failing   # skip fixtures at 1.0
    .venv/bin/python eval/show_wrong_statuses.py --summary        # one line per fixture
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-failing", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--latest", default=str(ROOT / "eval" / "results" / "_latest.json"))
    args = ap.parse_args()

    p = Path(args.latest)
    if not p.exists():
        print(f"No file at {p}", file=sys.stderr)
        return 2
    data = json.loads(p.read_text())

    print(f"agg_recall={data.get('agg_recall')}  "
          f"agg_status={data.get('agg_status_correctness')}  "
          f"p95_modern={data.get('p95_modern_clean_ms')}ms  "
          f"pass={data.get('pass')}  "
          f"llm_calls={data.get('total_llm_calls')} (${data.get('total_cost_usd')})\n")

    for r in data.get("results", []):
        sc = r.get("status_correctness")
        wrong = r.get("status_wrong") or {}
        if args.only_failing and (sc is None or sc == 1.0):
            continue
        label = r["label"][:60]
        if args.summary:
            tag = f"{sc:.2f}" if sc is not None else "—"
            print(f"  {tag}  {label}  ({len(wrong)} wrong)")
        else:
            print(f"\n{label}: status_correctness={sc}")
            for n, msg in wrong.items():
                print(f"  Item {n}: {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
