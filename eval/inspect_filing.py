"""Quick inspector: call /extract on a fixture and print compact item info.

Usage:
    .venv/bin/python eval/inspect_filing.py CIK ACCESSION [item_numbers...]
    .venv/bin/python eval/inspect_filing.py --label "BRK"           # match by label substring

Examples:
    .venv/bin/python eval/inspect_filing.py 1067983 0001193125-26-083899
    .venv/bin/python eval/inspect_filing.py 1067983 0001193125-26-083899 10 11 14
    .venv/bin/python eval/inspect_filing.py --label "kura" 10 11 12 13 14

Server defaults to http://localhost:8000; override with --url.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def _load_fixtures() -> list[dict]:
    fp = ROOT / "eval" / "fixtures" / "filings.jsonl"
    return [json.loads(l) for l in fp.read_text().splitlines() if l.strip()]


def _find_by_label(substring: str) -> dict | None:
    s = substring.lower()
    for fx in _load_fixtures():
        if s in fx["label"].lower():
            return fx
    return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cik", nargs="?")
    ap.add_argument("accession", nargs="?")
    ap.add_argument("items", nargs="*", help="item numbers to focus on; default = all")
    ap.add_argument("--label", help="match fixture by label substring instead of cik/accession")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--head", type=int, default=400, help="content_text head chars to show per item")
    args = ap.parse_args()

    if args.label:
        fx = _find_by_label(args.label)
        if not fx:
            print(f"No fixture matched label substring: {args.label!r}", file=sys.stderr)
            return 2
        cik = fx["cik"]
        acc = fx["accession_number"]
        body = {"file_url": fx["file_url"]} if "file_url" in fx else {"cik": cik, "accession_number": acc}
        print(f"# Fixture: {fx['label']}")
    elif args.cik and args.accession:
        body = {"cik": args.cik, "accession_number": args.accession}
    else:
        ap.error("provide CIK + ACCESSION, or --label")

    focus = set(args.items) if args.items else None

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{args.url}/extract", json=body)
        r.raise_for_status()
        data = r.json()

    print(f"# {data['filing'].get('company_name')} | form={data['filing'].get('form')} | "
          f"period={data['filing'].get('period_of_report')}")
    s = data["stats"]
    print(f"# stats: items={s['items_extracted']}E + {s['items_incorporated_by_reference']}IBR + "
          f"{s['items_not_applicable']}NA + {s['items_reserved']}R | missing={s['items_missing']} | "
          f"strategies={s['strategies']} | llm={s['llm_calls']} (${s['estimated_cost_usd']}) | "
          f"{s['duration_ms']}ms")

    print()
    for it in data["items"]:
        if focus and it["item_number"] not in focus:
            continue
        rng = it["char_range"]
        size = rng["end"] - rng["start"]
        print(f"--- Item {it['item_number']:3s} {it['part']:3s} | {it['status']:30s} | "
              f"size={size:>6} | resolved_by={it['resolved_by']}")
        head = it["content_text"][: args.head].replace("\n", "\\n")
        print(f"    text[:{args.head}]: {head!r}")
    if data.get("warnings"):
        print(f"\n# warnings ({len(data['warnings'])}):")
        for w in data["warnings"]:
            print(f"  - {w.get('code')}: {w.get('message', '')[:150]}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
