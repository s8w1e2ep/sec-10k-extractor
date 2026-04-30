"""Eval harness — runs all fixtures against /extract, writes a markdown report.

Usage:
    SEC_CONTACT_EMAIL='you@example.com' .venv/bin/uvicorn server.main:app --port 8000 &
    .venv/bin/python eval/run_eval.py http://localhost:8000

Pass-bar (per spec §5.2):
- items_recall ≥ 0.90 across the eval set
- status_correctness ≥ 0.85 on fixtures with expected overrides
- p95 latency ≤ 30 s on modern_clean fixtures
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import quantiles

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extractor.canonical_items import expected_items_for_period


def _expected_items(fixture: dict, filing_meta: dict) -> set[str]:
    if "expected_items_present" in fixture:
        return set(fixture["expected_items_present"])
    period_str = (
        fixture.get("period_of_report")
        or filing_meta.get("period_of_report")
    )
    period = None
    if period_str:
        try:
            period = date.fromisoformat(period_str)
        except ValueError:
            period = None
    return {ci.item_number for ci in expected_items_for_period(period, only_required=True)}


async def run_one(client: httpx.AsyncClient, base_url: str, fx: dict) -> dict:
    body = (
        {"file_url": fx["file_url"]}
        if "file_url" in fx
        else {"cik": fx["cik"], "accession_number": fx["accession_number"]}
    )
    t0 = time.monotonic()
    try:
        r = await client.post(f"{base_url}/extract", json=body, timeout=120.0)
        wall_ms = int((time.monotonic() - t0) * 1000)
        r.raise_for_status()
        result = r.json()
    except Exception as e:
        return {
            "label": fx["label"],
            "category": fx["category"],
            "error": str(e),
            "wall_ms": int((time.monotonic() - t0) * 1000),
        }

    items = result.get("items", [])
    stats = result.get("stats", {})
    warnings = result.get("warnings", [])
    filing = result.get("filing", {})

    expected = _expected_items(fx, filing)
    found = {it["item_number"] for it in items}
    recall = len(found & expected) / len(expected) if expected else 0.0

    overrides = fx.get("expected_status_overrides", {})
    status_by_num = {it["item_number"]: it["status"] for it in items}
    if overrides:
        correct = sum(1 for n, s in overrides.items() if status_by_num.get(n) == s)
        status_correctness: float | None = correct / len(overrides)
        wrong = {
            n: f"expected={s}, got={status_by_num.get(n, 'MISSING')}"
            for n, s in overrides.items() if status_by_num.get(n) != s
        }
    else:
        status_correctness = None
        wrong = {}

    return {
        "label": fx["label"],
        "category": fx["category"],
        "items_found": len(found),
        "items_expected": len(expected),
        "items_missing": sorted(expected - found),
        "items_recall": round(recall, 3),
        "status_correctness": round(status_correctness, 3) if status_correctness is not None else None,
        "status_wrong": wrong,
        "strategies": stats.get("strategies", {}),
        "format": stats.get("format"),
        "duration_ms": stats.get("duration_ms", 0),
        "wall_ms": wall_ms,
        "fetch_ms": stats.get("fetch_ms", 0),
        "llm_calls": stats.get("llm_calls", 0),
        "cost_usd": stats.get("estimated_cost_usd", 0.0),
        "n_warnings": len(warnings),
        "warnings": warnings,
    }


def _p95(xs: list[int]) -> int | None:
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    qs = quantiles(xs, n=20, method="inclusive")
    return int(qs[18])


def write_report(results: list[dict], out_dir: Path) -> tuple[Path, dict]:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_md = out_dir / f"eval-{ts}.md"
    out_json = out_dir / "_latest.json"

    valid = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]

    agg_recall = sum(r["items_recall"] for r in valid) / len(valid) if valid else 0.0
    with_overrides = [r for r in valid if r["status_correctness"] is not None]
    agg_status = (
        sum(r["status_correctness"] for r in with_overrides) / len(with_overrides)
        if with_overrides else None
    )
    modern_clean_lats = [
        r["duration_ms"] for r in valid if "modern_clean" in r["category"]
    ]
    p95_modern = _p95(modern_clean_lats)

    total_cost = sum(r["cost_usd"] for r in valid)
    total_llm = sum(r["llm_calls"] for r in valid)

    pass_recall = agg_recall >= 0.90
    pass_status = (agg_status is None) or (agg_status >= 0.85)
    pass_latency = (p95_modern is None) or (p95_modern <= 30000)

    md: list[str] = []
    md.append(f"# Eval run — {ts}")
    md.append("")
    md.append("## Headline")
    md.append("")
    md.append(f"- Fixtures: {len(results)} total ({len(valid)} ok, {len(errors)} error)")
    md.append(f"- **items_recall (mean across fixtures): {agg_recall:.3f}**")
    if agg_status is not None:
        md.append(
            f"- **status_correctness (mean across {len(with_overrides)} fixtures with overrides): {agg_status:.3f}**"
        )
    else:
        md.append("- status_correctness: n/a (no fixtures had `expected_status_overrides`)")
    if p95_modern is not None:
        md.append(f"- p95 latency on modern_clean: {p95_modern} ms (n={len(modern_clean_lats)})")
    md.append(f"- Total LLM calls: {total_llm}; total cost: ${total_cost:.4f}")
    md.append("")

    md.append("## Pass-bar")
    md.append("")
    md.append(f"| Check | Threshold | Result | Status |")
    md.append(f"|---|---|---|---|")
    md.append(f"| items_recall | ≥ 0.90 | {agg_recall:.3f} | {'PASS' if pass_recall else 'FAIL'} |")
    if agg_status is not None:
        md.append(f"| status_correctness | ≥ 0.85 | {agg_status:.3f} | {'PASS' if pass_status else 'FAIL'} |")
    if p95_modern is not None:
        md.append(f"| p95 latency modern_clean | ≤ 30000 ms | {p95_modern} | {'PASS' if pass_latency else 'FAIL'} |")
    md.append("")

    md.append("## Per-category breakdown")
    md.append("")
    cats = sorted({c for r in valid for c in r["category"]})
    md.append("| Category | n | mean recall | mean duration (ms) | LLM calls |")
    md.append("|---|---|---|---|---|")
    for cat in cats:
        rs = [r for r in valid if cat in r["category"]]
        if not rs:
            continue
        mean_r = sum(r["items_recall"] for r in rs) / len(rs)
        mean_d = sum(r["duration_ms"] for r in rs) / len(rs)
        n_llm = sum(r["llm_calls"] for r in rs)
        md.append(f"| {cat} | {len(rs)} | {mean_r:.3f} | {int(mean_d)} | {n_llm} |")
    md.append("")

    md.append("## Per-fixture")
    md.append("")
    md.append(
        "| # | Fixture | Categories | Recall | Status | Format | duration (ms) | Strategies | Warn |"
    )
    md.append("|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(valid, 1):
        strats = r.get("strategies") or {}
        strats_str = "/".join(f"{k[0]}={v}" for k, v in strats.items() if v) or "-"
        cats_str = ",".join(r["category"])
        sc = r["status_correctness"]
        sc_str = f"{sc:.2f}" if sc is not None else "-"
        md.append(
            f"| {i} | {r['label'][:55]} | {cats_str} | {r['items_recall']:.2f} | {sc_str} | "
            f"{r['format']} | {r['duration_ms']} | {strats_str} | {r['n_warnings']} |"
        )
    md.append("")

    if errors:
        md.append("## Errors")
        md.append("")
        for e in errors:
            md.append(f"- **{e['label']}**: {e['error']}")
        md.append("")

    md.append("## Detail per fixture")
    md.append("")
    for r in valid:
        md.append(f"### {r['label']}")
        md.append("")
        md.append(f"- categories: {', '.join(r['category'])}")
        md.append(f"- format: `{r['format']}`")
        md.append(f"- items found / expected: {r['items_found']} / {r['items_expected']}")
        if r["items_missing"]:
            md.append(f"- items missing: {', '.join(r['items_missing'])}")
        md.append(f"- strategies: {r['strategies']}")
        md.append(f"- duration: {r['duration_ms']} ms (server) / {r['wall_ms']} ms (wall)")
        if r["status_correctness"] is not None:
            md.append(f"- status correctness: {r['status_correctness']:.3f}")
            if r["status_wrong"]:
                md.append(f"- status mismatches:")
                for n, msg in r["status_wrong"].items():
                    md.append(f"    - Item {n}: {msg}")
        if r["warnings"]:
            md.append(f"- warnings ({len(r['warnings'])}):")
            for w in r["warnings"]:
                md.append(f"    - `{w.get('code')}`: {w.get('message', '')[:200]}")
        md.append("")

    out_md.write_text("\n".join(md))
    summary = {
        "timestamp": ts,
        "agg_recall": round(agg_recall, 3),
        "agg_status_correctness": round(agg_status, 3) if agg_status is not None else None,
        "p95_modern_clean_ms": p95_modern,
        "total_cost_usd": round(total_cost, 4),
        "total_llm_calls": total_llm,
        "n_fixtures": len(results),
        "n_errors": len(errors),
        "pass": pass_recall and pass_status and pass_latency,
        "results": valid,
    }
    out_json.write_text(json.dumps(summary, indent=2))
    return out_md, summary


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url", nargs="?", default="http://localhost:8000")
    parser.add_argument("--fixtures", default=str(ROOT / "eval" / "fixtures" / "filings.jsonl"))
    parser.add_argument("--out", default=str(ROOT / "eval" / "results"))
    args = parser.parse_args()

    fixtures: list[dict] = []
    with open(args.fixtures) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            fixtures.append(json.loads(line))

    print(f"Running {len(fixtures)} fixtures against {args.base_url}\n")

    async with httpx.AsyncClient() as client:
        results: list[dict] = []
        for fx in fixtures:
            print(f"  → {fx['label'][:80]}")
            r = await run_one(client, args.base_url, fx)
            if "error" in r:
                print(f"      ERROR: {r['error']}")
            else:
                sc = r["status_correctness"]
                sc_str = f"  status={sc:.2f}" if sc is not None else ""
                print(
                    f"      recall={r['items_recall']:.2f}{sc_str}  "
                    f"duration={r['duration_ms']}ms  warn={r['n_warnings']}"
                )
            results.append(r)

    out, summary = write_report(results, Path(args.out))
    print(f"\nReport: {out}")
    print(f"Summary: agg_recall={summary['agg_recall']}, "
          f"agg_status={summary['agg_status_correctness']}, "
          f"p95_modern={summary['p95_modern_clean_ms']}, "
          f"pass={summary['pass']}")
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
